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
