from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import supabase
from app.services.auth_service import require_admin_or_leader

router = APIRouter(prefix="/api/admin-portal", tags=["admin-portal"])


class TodayMessageBody(BaseModel):
    target_date: str
    message: str


@router.get("/home")
def get_admin_home(current_user: dict = Depends(require_admin_or_leader)):
    today = date.today().isoformat()

    message_res = (
        supabase
        .table("portal_messages")
        .select("*")
        .eq("target_date", today)
        .limit(1)
        .execute()
    )

    shift_res = (
        supabase
        .table("shift_days")
        .select("*, shift_entries(*, staff_members(*))")
        .eq("shift_date", today)
        .execute()
    )

    today_message = ""
    if message_res.data:
        today_message = message_res.data[0].get("message") or ""

    return {
        "todayDate": today,
        "todayMessage": today_message,
        "todayShift": shift_res.data[0] if shift_res.data else None,
    }


@router.post("/today-message")
def save_today_message(
    payload: TodayMessageBody,
    current_user: dict = Depends(require_admin_or_leader),
):
    user_id = current_user["user_id"]
    target_date = payload.target_date
    message = payload.message.strip()

    if not target_date:
        raise HTTPException(status_code=400, detail="target_date は必須です。")

    existing = (
        supabase
        .table("portal_messages")
        .select("id")
        .eq("target_date", target_date)
        .limit(1)
        .execute()
    )

    if existing.data:
        res = (
            supabase
            .table("portal_messages")
            .update({
                "message": message,
                "updated_by": user_id,
            })
            .eq("target_date", target_date)
            .execute()
        )
    else:
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

    return {"ok": True, "data": res.data}


@router.get("/calendar")
def get_admin_calendar(
    year: int,
    month: int,
    current_user: dict = Depends(require_admin_or_leader),
):
    start_date = date(year, month, 1).isoformat()
    if month == 12:
        end_date = date(year + 1, 1, 1).isoformat()
    else:
        end_date = date(year, month + 1, 1).isoformat()

    res = (
        supabase
        .table("shift_days")
        .select("*, shift_entries(*, staff_members(*))")
        .gte("shift_date", start_date)
        .lt("shift_date", end_date)
        .order("shift_date")
        .execute()
    )

    return {"days": res.data or []}
