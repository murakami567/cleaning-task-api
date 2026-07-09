from datetime import date, timedelta
import os

import requests
from fastapi import APIRouter, Depends, HTTPException

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
    """
    # バリデーション目的で月範囲を作成する。
    _month_range(year, month)

    base_url = os.getenv("GUSK_PROPERTY_MANAGEMENT_URL", "https://gusk-property-management.onrender.com").rstrip("/")
    url = f"{base_url}/api/constructions/public-schedules"

    try:
      res = requests.get(url, params={"year": year, "month": month}, timeout=15)
      if res.status_code >= 400:
          logger.error(
              f"construction schedule proxy failed: status={res.status_code} body={res.text[:500]}"
          )
          raise HTTPException(status_code=502, detail="工事・リフォーム予定の取得に失敗しました。")
      data = res.json()
    except HTTPException:
      raise
    except Exception as e:
      logger.error(f"construction schedule proxy failed: {e}", exc_info=True)
      raise HTTPException(status_code=502, detail="工事・リフォーム予定の取得に失敗しました。")

    items = data.get("items") if isinstance(data, dict) else []
    if not isinstance(items, list):
        items = []

    logger.info(f"get_construction_schedules: year={year} month={month} count={len(items)}")
    return {"items": items}
