from collections import defaultdict
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

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

PAGE_SIZE = 1000


class CalendarMessageBody(BaseModel):
    target_date: str
    message: str


def _month_range(year: int, month: int) -> tuple[str, str]:
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month + 1, 1)
    return start.isoformat(), end.isoformat()


def _date_key(value) -> str:
    return str(value or "")[:10]


def _fetch_cleaning_tasks(start_date: str, end_date: str) -> list[dict]:
    """Fetch every cleaning task in the month, beyond Supabase's 1000-row response limit."""
    rows: list[dict] = []
    offset = 0

    while True:
        res = (
            supabase
            .table("cleaning_tasks")
            .select("id,task_date,status")
            .gte("task_date", start_date)
            .lt("task_date", end_date)
            .order("task_date")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
        )
        page = res.data or []
        rows.extend(page)

        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    return rows


@router.get("/company-calendar-summary")
def get_company_calendar_summary(
    year: int,
    month: int,
    current_user: dict = Depends(require_admin_or_leader),
):
    start_date, end_date = _month_range(year, month)

    try:
        task_rows = _fetch_cleaning_tasks(start_date, end_date)
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
    for row in task_rows:
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


def _validate_message(payload: CalendarMessageBody) -> tuple[str, str]:
    target_date = payload.target_date.strip()
    message = payload.message.strip()
    if not target_date:
        raise HTTPException(status_code=400, detail="対象日は必須です。")
    try:
        date.fromisoformat(target_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="対象日の形式が正しくありません。")
    if not message:
        raise HTTPException(status_code=400, detail="連絡内容は必須です。")
    return target_date, message


@router.post("/calendar-messages")
def create_calendar_message(
    payload: CalendarMessageBody,
    current_user: dict = Depends(require_admin_or_leader),
):
    target_date, message = _validate_message(payload)
    user_id = current_user["user_id"]
    try:
        res = (
            supabase
            .table("portal_messages")
            .insert({
                "target_date": target_date,
                "message": message,
                "updated_by": user_id,
            })
            .execute()
        )
    except Exception as e:
        logger.error(f"create_calendar_message failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="連絡事項の登録に失敗しました。")
    return {"ok": True, "data": res.data}


@router.put("/calendar-messages/{message_id}")
def update_calendar_message(
    message_id: str,
    payload: CalendarMessageBody,
    current_user: dict = Depends(require_admin_or_leader),
):
    target_date, message = _validate_message(payload)
    user_id = current_user["user_id"]
    try:
        res = (
            supabase
            .table("portal_messages")
            .update({
                "target_date": target_date,
                "message": message,
                "updated_by": user_id,
                "updated_at": "now()",
            })
            .eq("id", message_id)
            .execute()
        )
    except Exception as e:
        logger.error(f"update_calendar_message failed: id={message_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="連絡事項の更新に失敗しました。")
    return {"ok": True, "data": res.data}


@router.delete("/calendar-messages/{message_id}")
def delete_calendar_message(
    message_id: str,
    current_user: dict = Depends(require_admin_or_leader),
):
    try:
        res = (
            supabase
            .table("portal_messages")
            .delete()
            .eq("id", message_id)
            .execute()
        )
    except Exception as e:
        logger.error(f"delete_calendar_message failed: id={message_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="連絡事項の削除に失敗しました。")
    return {"ok": True, "data": res.data}
