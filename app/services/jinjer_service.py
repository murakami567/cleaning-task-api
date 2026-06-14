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

# Jinjer側の打刻APIパスは契約/APIバージョンで差が出る可能性があるため環境変数で変更可能にする。
# 例: /v2/employees/attendances, /v2/employees/time-records など
JINJER_ATTENDANCE_ENDPOINT = os.getenv(
    "JINJER_ATTENDANCE_ENDPOINT",
    "/v2/employees/attendances",
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
    """
    Jinjer打刻APIのレスポンス差分を吸収して、保存用の共通形へ寄せる。
    想定キー例:
      employee_id / staff_id
      date / work_date / target_date
      clock_in_at / clock_in / start_time / attendance_start
      clock_out_at / clock_out / end_time / attendance_end
    """
    employee_id = str(
        _pick_first(row, ["employee_id", "staff_id", "employee_code", "code"]) or ""
    ).strip()

    work_date = _pick_first(row, ["date", "work_date", "target_date", "attendance_date"])

    clock_in_at = _pick_first(
        row,
        [
            "clock_in_at",
            "clock_in",
            "check_in_at",
            "check_in",
            "start_at",
            "start_time",
            "attendance_start",
        ],
    )
    clock_out_at = _pick_first(
        row,
        [
            "clock_out_at",
            "clock_out",
            "check_out_at",
            "check_out",
            "end_at",
            "end_time",
            "attendance_end",
        ],
    )

    # ネストされた打刻オブジェクトにも対応
    attendance = row.get("attendance") or row.get("time_record") or row.get("work_record") or {}
    if isinstance(attendance, dict):
        if not clock_in_at:
            clock_in_at = _pick_first(attendance, ["clock_in_at", "clock_in", "start_at", "start_time"])
        if not clock_out_at:
            clock_out_at = _pick_first(attendance, ["clock_out_at", "clock_out", "end_at", "end_time"])
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
    Jinjerから打刻データを取得する。

    JINJER_ATTENDANCE_ENDPOINT でAPIパスを変更可能。
    start_date / end_date は YYYY-MM-DD。
    """
    target_end_date = end_date or start_date
    items: list[dict[str, Any]] = []
    page = 1
    safety_max_pages = 200

    while page <= safety_max_pages:
        params = {
            "start_date": start_date,
            "end_date": target_end_date,
            "date_from": start_date,
            "date_to": target_end_date,
            "page": page,
        }

        try:
            res = _authorized_get(JINJER_ATTENDANCE_ENDPOINT, params=params, timeout=60)
        except Exception as e:
            logger.error(f"jinjer attendances request failed: page={page} {e}", exc_info=True)
            raise HTTPException(status_code=502, detail="Jinjer 打刻取得に失敗しました。")

        if res.status_code != 200:
            logger.error(
                f"jinjer attendances returned {res.status_code}: endpoint={JINJER_ATTENDANCE_ENDPOINT} body={res.text[:700]}"
            )
            raise HTTPException(
                status_code=502,
                detail=f"Jinjer 打刻取得失敗: {res.status_code}",
            )

        body = res.json() or {}
        page_data = body.get("data") or body.get("items") or body.get("attendances") or []

        # employee配下にattendancesが入る形式にも対応
        flat_rows: list[dict[str, Any]] = []
        for row in page_data:
            if not isinstance(row, dict):
                continue
            employee_id = row.get("employee_id") or row.get("staff_id")
            nested = row.get("attendances") or row.get("time_records") or row.get("work_records")
            if isinstance(nested, list):
                for child in nested:
                    if isinstance(child, dict):
                        flat_rows.append({**child, "employee_id": employee_id or child.get("employee_id")})
            else:
                flat_rows.append(row)

        for row in flat_rows:
            normalized = _normalize_attendance_row(row)
            if normalized:
                items.append(normalized)

        if len(page_data) < 20:
            break
        page += 1

    logger.info(
        f"jinjer attendances fetched: start={start_date} end={target_end_date} endpoint={JINJER_ATTENDANCE_ENDPOINT} pages={page} items={len(items)}"
    )
    return items
