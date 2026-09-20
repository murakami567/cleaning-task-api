from collections import defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException

from app.db import supabase
from app.logger import get_logger
from app.services.auth_service import require_admin_or_leader

router = APIRouter(prefix="/api/admin-portal", tags=["admin-portal"])
logger = get_logger(__name__)

EXCLUDED_CLEANING_STATUSES = {
    "CXL",
    "キャンセル",
    "cancelled",
    "Cancelled",
    "canceled",
    "Canceled",
}


def _month_range(year: int, month: int) -> tuple[str, str]:
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month + 1, 1)
    return start.isoformat(), end.isoformat()


def _date_key(value) -> str:
    return str(value or "")[:10]


@router.get("/company-calendar-summary")
def get_company_calendar_summary(
    year: int,
    month: int,
    current_user: dict = Depends(require_admin_or_leader),
):
    start_date, end_date = _month_range(year, month)

    try:
        task_res = (
            supabase
            .table("cleaning_tasks")
            .select("id,task_date,status")
            .gte("task_date", start_date)
            .lt("task_date", end_date)
            .execute()
        )
        message_res = (
            supabase
            .table("portal_messages")
            .select("id,target_date,message,updated_at")
            .gte("target_date", start_date)
            .lt("target_date", end_date)
            .order("target_date")
            .order("updated_at", desc=True)
            .execute()
        )
    except Exception as e:
        logger.error(
            f"get_company_calendar_summary failed: year={year} month={month} {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="カレンダー集計の取得に失敗しました。")

    cleaning_counts: dict[str, int] = defaultdict(int)
    for row in task_res.data or []:
        status = str(row.get("status") or "")
        if status in EXCLUDED_CLEANING_STATUSES:
            continue
        task_date = _date_key(row.get("task_date"))
        if task_date:
            cleaning_counts[task_date] += 1

    messages = []
    for row in message_res.data or []:
        target_date = _date_key(row.get("target_date"))
        if not target_date:
            continue
        messages.append(
            {
                "id": str(row.get("id") or ""),
                "target_date": target_date,
                "message": row.get("message") or "",
                "updated_at": row.get("updated_at"),
            }
        )

    return {
        "cleaning_counts": dict(cleaning_counts),
        "messages": messages,
    }
