from datetime import date, timedelta
import os

import requests
from fastapi import APIRouter, Depends

from app.logger import get_logger
from app.services.auth_service import require_admin_or_leader


router = APIRouter(prefix="/api/admin-portal", tags=["admin-portal"])
logger = get_logger(__name__)


def _month_range(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    return start, end


@router.get("/construction-schedules")
def get_construction_schedules(
    year: int,
    month: int,
    current_user: dict = Depends(require_admin_or_leader),
):
    """
    管理ホームのスケジュールカレンダー用。
    gusk-property-management の工事・リフォーム予定を取得して返す。
    外部サービス停止時は管理ホーム全体を壊さず、空配列と警告を返す。
    """
    _month_range(year, month)

    base_url = os.getenv(
        "GUSK_PROPERTY_MANAGEMENT_URL",
        "https://gusk-property-management.onrender.com",
    ).rstrip("/")
    url = f"{base_url}/api/constructions/public-schedules"

    try:
        res = requests.get(url, params={"year": year, "month": month}, timeout=15)
        if res.status_code >= 400:
            logger.warning(
                f"construction schedule proxy unavailable: status={res.status_code} body={res.text[:500]}"
            )
            return {
                "items": [],
                "warning": "工事・リフォーム予定を一時的に取得できませんでした。",
            }
        data = res.json()
    except Exception as e:
        logger.warning(f"construction schedule proxy unavailable: {e}", exc_info=True)
        return {
            "items": [],
            "warning": "工事・リフォーム予定を一時的に取得できませんでした。",
        }

    items = data.get("items") if isinstance(data, dict) else []
    if not isinstance(items, list):
        items = []

    logger.info(f"get_construction_schedules: year={year} month={month} count={len(items)}")
    return {"items": items}
