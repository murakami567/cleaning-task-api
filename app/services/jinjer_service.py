import os
import time
from typing import Any

import requests
from fastapi import HTTPException

from app.logger import get_logger

logger = get_logger(__name__)

JINJER_BASE_URL = os.getenv("JINJER_BASE_URL", "https://api.jinjer.biz").rstrip("/")
JINJER_API_KEY = os.getenv("JINJER_API_KEY", "")
JINJER_SECRET_KEY = os.getenv("JINJER_SECRET_KEY", "")

# 勤怠 日次打刻データ
JINJER_ATTENDANCE_ENDPOINT = os.getenv(
    "JINJER_ATTENDANCE_ENDPOINT",
    "/v1/kintai-daily-attendances",
)

# トークンは 4 時間有効。安全側で 3.5 時間でキャッシュ切れにする。
_TOKEN_TTL_SEC = 3.5 * 60 * 60
_token_cache: dict[str, Any] = {"token": "", "expires_at": 0.0}


def _get_access_token() -> str:
    """Jinjer の access_token を取得。同プロセス内でメモリキャッシュ。"""
    now = time.time()
    if _token_cache["token"] and _token_cache["expires_at"] > now:
        return _token_cache["token"]

    if not JINJER_API_KEY or not JINJER_SECRET_KEY:
        raise HTTPException(
            status_code=500,
            detail="JINJER_API_KEY / JINJER_SECRET_KEY が設定されていません。",
        )

    try:
        res = requests.get(
            f"{JINJER_BASE_URL}/v2/token",
            headers={
                "Content-Type": "application/json",
                "X-API-KEY": JINJER_API_KEY,
                "X-SECRET-KEY": JINJER_SECRET_KEY,
            },
            timeout=30,
        )
    except Exception as e:
        logger.error(f"jinjer token request failed: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail="Jinjer 認証リクエストに失敗しました。")

    if res.status_code != 200:
        logger.error(f"jinjer token returned {res.status_code}: {res.text[:300]}")
        raise HTTPException(status_code=502, detail=f"Jinjer 認証失敗: {res.status_code}")

    body = res.json() or {}
    token = (body.get("data") or {}).get("access_token") or body.get("access_token")
    if not token:
        logger.error(f"jinjer token missing in response: {body}")
        raise HTTPException(status_code=502, detail="Jinjer 認証レスポンスにトークンがありません。")

    _token_cache["token"] = token
    _token_cache["expires_at"] = now + _TOKEN_TTL_SEC
    return token


def _authorized_get(path: str, params: dict[str, Any], timeout: int = 60) -> requests.Response:
    token = _get_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{JINJER_BASE_URL}{path if path.startswith('/') else '/' + path}"

    res = requests.get(url, params=params, headers=headers, timeout=timeout)
    if res.status_code == 401:
        _token_cache["token"] = ""
        _token_cache["expires_at"] = 0.0
        token = _get_access_token()
        headers["Authorization"] = f"Bearer {token}"
        res = requests.get(url, params=params, headers=headers, timeout=timeout)
    return res


def fetch_work_schedules(month: str) -> list[dict[str, Any]]:
    """
    /v2/employees/work-schedules を全ページ取得して、
    (employee_id, date, start, end) のフラットな配列で返す。

    month は "YYYY-MM" 形式。
    """
    items: list[dict[str, Any]] = []
    page = 1
    safety_max_pages = 200

    while page <= safety_max_pages:
        try:
            res = _authorized_get(
                "/v2/employees/work-schedules",
                params={"month": month, "page": page},
                timeout=60,
            )
        except Exception as e:
            logger.error(f"jinjer work-schedules request failed: page={page} {e}", exc_info=True)
            raise HTTPException(status_code=502, detail="Jinjer シフト取得に失敗しました。")

        if res.status_code != 200:
            logger.error(
                f"jinjer work-schedules returned {res.status_code}: {res.text[:500]}"
            )
            raise HTTPException(
                status_code=502,
                detail=f"Jinjer シフト取得失敗: {res.status_code}",
            )

        body = res.json() or {}
        page_data = body.get("data") or []

        for employee in page_data:
            employee_id = str(employee.get("employee_id") or "").strip()
            if not employee_id:
                continue
            for entry in employee.get("work_schedules") or []:
                date = entry.get("date")
                ws = entry.get("work_schedule") or {}
                start = ws.get("start")
                end = ws.get("end")
                if not date:
                    continue
                items.append({
                    "employee_id": employee_id,
                    "date": date,
                    "start": start,
                    "end": end,
                })

        if len(page_data) < 20:
            break
        page += 1

    logger.info(f"jinjer work-schedules fetched: month={month} pages={page} items={len(items)}")
    return items


def _pick_first(row: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _normalize_attendance_row(row: dict[str, Any]) -> dict[str, Any] | None:
    employee_id = str(
        _pick_first(row, ["employee_id", "employee-id", "staff_id", "employee_code", "code"]) or ""
    ).strip()

    work_date = _pick_first(row, ["date", "work_date", "target_date", "attendance_date"])

    clock_in_at = _pick_first(
        row,
        [
            "attended_at",
            "clock_in_at",
            "clock_in",
            "check_in_at",
            "check_in",
            "start_at",
            "start_time",
            "attendance_start",
            "begin_at",
            "begin_time",
        ],
    )
    clock_out_at = _pick_first(
        row,
        [
            "left_at",
            "clock_out_at",
            "clock_out",
            "check_out_at",
            "check_out",
            "end_at",
            "end_time",
            "attendance_end",
            "finish_at",
            "finish_time",
        ],
    )

    attendance = row.get("attendance") or row.get("time_record") or row.get("work_record") or {}
    if isinstance(attendance, dict):
        if not clock_in_at:
            clock_in_at = _pick_first(
                attendance,
                ["attended_at", "clock_in_at", "clock_in", "start_at", "start_time", "begin_at", "begin_time"],
            )
        if not clock_out_at:
            clock_out_at = _pick_first(
                attendance,
                ["left_at", "clock_out_at", "clock_out", "end_at", "end_time", "finish_at", "finish_time"],
            )
        if not work_date:
            work_date = _pick_first(attendance, ["date", "work_date", "target_date"])

    if not employee_id or not work_date:
        return None

    return {
        "employee_id": employee_id,
        "work_date": str(work_date)[:10],
        "clock_in_at": clock_in_at,
        "clock_out_at": clock_out_at,
        "raw_data": row,
    }


def fetch_attendances(start_date: str, end_date: str | None = None) -> list[dict[str, Any]]:
    """
    Jinjer 勤怠 日次打刻データを取得する。

    /v1/kintai-daily-attendances は date=YYYY-MM-DD が必須。
    employee_id 配下の attendances[] をフラット化して返す。
    """
    target_end_date = end_date or start_date
    items: list[dict[str, Any]] = []
    safety_max_pages = 200

    # 通常は単日同期。期間指定時は日付ごとに取得する。
    from datetime import date as date_cls, timedelta

    start_d = date_cls.fromisoformat(start_date)
    end_d = date_cls.fromisoformat(target_end_date)
    current = start_d

    while current <= end_d:
        target = current.isoformat()
        page = 1
        while page <= safety_max_pages:
            params = {
                "date": target,
                "page": page,
            }

            try:
                res = _authorized_get(JINJER_ATTENDANCE_ENDPOINT, params=params, timeout=60)
            except Exception as e:
                logger.error(f"jinjer attendances request failed: date={target} page={page} {e}", exc_info=True)
                raise HTTPException(status_code=502, detail="Jinjer 打刻取得に失敗しました。")

            if res.status_code != 200:
                logger.error(
                    f"jinjer attendances returned {res.status_code}: endpoint={JINJER_ATTENDANCE_ENDPOINT} params={params} body={res.text[:700]}"
                )
                raise HTTPException(
                    status_code=502,
                    detail=f"Jinjer 打刻取得失敗: {res.status_code}",
                )

            body = res.json() or {}
            page_data = body.get("data") or body.get("items") or body.get("attendances") or []

            flat_rows: list[dict[str, Any]] = []
            for row in page_data:
                if not isinstance(row, dict):
                    continue
                employee_id = row.get("employee_id") or row.get("employee-id") or row.get("staff_id")
                nested = row.get("attendances") or row.get("time_records") or row.get("work_records")
                if isinstance(nested, list):
                    for child in nested:
                        if isinstance(child, dict):
                            flat_rows.append({**child, "employee_id": employee_id or child.get("employee_id")})
                else:
                    flat_rows.append(row)

            for row in flat_rows:
                normalized = _normalize_attendance_row(row)
                if not normalized:
                    continue
                work_date = normalized["work_date"]
                if start_date <= work_date <= target_end_date:
                    items.append(normalized)

            # Jinjer docs: page未指定で100件。100件未満で終了。
            if len(page_data) < 100:
                break
            page += 1

        current += timedelta(days=1)

    logger.info(
        f"jinjer attendances fetched: start={start_date} end={target_end_date} endpoint={JINJER_ATTENDANCE_ENDPOINT} items={len(items)}"
    )
    return items
