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


def fetch_work_schedules(month: str) -> list[dict[str, Any]]:
    """
    /v2/employees/work-schedules を全ページ取得して、
    (employee_id, date, start, end) のフラットな配列で返す。

    month は "YYYY-MM" 形式。
    """
    token = _get_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    items: list[dict[str, Any]] = []
    page = 1
    safety_max_pages = 200  # 暴走防止 (社員 4000 人想定で十分)

    while page <= safety_max_pages:
        try:
            res = requests.get(
                f"{JINJER_BASE_URL}/v2/employees/work-schedules",
                params={"month": month, "page": page},
                headers=headers,
                timeout=60,
            )
        except Exception as e:
            logger.error(f"jinjer work-schedules request failed: page={page} {e}", exc_info=True)
            raise HTTPException(status_code=502, detail="Jinjer シフト取得に失敗しました。")

        if res.status_code == 401:
            # トークン期限切れ等。リセットして再試行。
            _token_cache["token"] = ""
            _token_cache["expires_at"] = 0.0
            token = _get_access_token()
            headers["Authorization"] = f"Bearer {token}"
            continue

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

        # ページサイズが 20 未満ならそこで打ち切り
        if len(page_data) < 20:
            break
        page += 1

    logger.info(f"jinjer work-schedules fetched: month={month} pages={page} items={len(items)}")
    return items
