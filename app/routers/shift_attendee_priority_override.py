from datetime import date

from fastapi import APIRouter, HTTPException

from app.db import supabase
from app.logger import get_logger

router = APIRouter(tags=["shift-priority-override"])
logger = get_logger(__name__)

SHIFT_DAY_SELECT_WITH_PRIORITY = (
    "id, shift_date, note, "
    "shift_entries(id, shift_day_id, staff_id, status, start_time, end_time, assigned_area, note, "
    "staff_members(id, staff_code, staff_name, role, is_active, sort_order, "
    "available_property_ids, unchecked_property_ids))"
)


@router.get("/shifts")
def get_shifts_with_assignment_priority(shift_date: str | None = None):
    """担当者選択画面で優先表示に必要な物件設定を含めて返す。"""
    target_date = shift_date or date.today().isoformat()
    try:
        res = (
            supabase.table("shift_days")
            .select(SHIFT_DAY_SELECT_WITH_PRIORITY)
            .eq("shift_date", target_date)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        logger.info(
            f"priority shift fetch: shift_date={target_date} days={len(rows)}"
        )
        return rows
    except Exception as e:
        logger.error(
            f"priority shift fetch failed: shift_date={target_date} {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="shifts fetch failed")

@router.get("/shifts/batch")
def get_shifts_batch_with_assignment_priority(shift_dates: str):
    """複数日分の担当者候補を1回のDB問い合わせで返す。"""
    raw_dates = [value.strip() for value in shift_dates.split(",") if value.strip()]
    target_dates = list(dict.fromkeys(raw_dates))

    if not target_dates:
        return []
    if len(target_dates) > 180:
        raise HTTPException(status_code=400, detail="shift_dates must contain 180 dates or fewer")

    try:
        for value in target_dates:
            date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="shift_dates must use YYYY-MM-DD format")

    try:
        res = (
            supabase.table("shift_days")
            .select(SHIFT_DAY_SELECT_WITH_PRIORITY)
            .in_("shift_date", target_dates)
            .order("shift_date")
            .execute()
        )
        rows = res.data or []
        logger.info(
            f"priority shift batch fetch: requested={len(target_dates)} days={len(rows)}"
        )
        return rows
    except Exception as e:
        logger.error(
            f"priority shift batch fetch failed: requested={len(target_dates)} {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="shift batch fetch failed")

