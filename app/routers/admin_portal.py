from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import supabase
from app.services.auth_service import require_admin_or_leader

router = APIRouter(prefix="/api/admin-portal", tags=["admin-portal"])


class TodayMessageBody(BaseModel):
    target_date: str
    message: str


class PortalScheduleBody(BaseModel):
    start_date: str
    end_date: str
    assignee_ids: list[str] = []
    assignee_names: list[str] = []
    title: str
    description: str = ""


@router.get("/home")
def get_admin_home(current_user: dict = Depends(require_admin_or_leader)):
    today = date.today().isoformat()

    message_res = (
        supabase
        .table("portal_messages")
        .select("*")
        .eq("target_date", today)
        .order("updated_at", desc=True)
        .execute()
    )

    shift_res = (
        supabase
        .table("shift_days")
        .select("*, shift_entries(*, staff_members(*))")
        .eq("shift_date", today)
        .execute()
    )

    return {
        "todayDate": today,
        "todayMessages": message_res.data or [],
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

    if not message:
        raise HTTPException(status_code=400, detail="message は必須です。")

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


@router.get("/calendar-schedules")
def get_calendar_schedules(
    year: int,
    month: int,
    current_user: dict = Depends(require_admin_or_leader),
):
    month_start = date(year, month, 1)
    if month == 12:
        month_end = date(year + 1, 1, 1)
    else:
        month_end = date(year, month + 1, 1)

    res = (
        supabase
        .table("portal_schedules")
        .select("*")
        .lte("start_date", month_end.isoformat())
        .gte("end_date", month_start.isoformat())
        .order("start_date")
        .execute()
    )

    return {"schedules": res.data or []}


@router.post("/calendar-schedules")
def create_calendar_schedule(
    payload: PortalScheduleBody,
    current_user: dict = Depends(require_admin_or_leader),
):
    user_id = current_user["user_id"]

    if not payload.start_date:
        raise HTTPException(status_code=400, detail="start_date は必須です。")
    if not payload.end_date:
        raise HTTPException(status_code=400, detail="end_date は必須です。")
    if payload.end_date < payload.start_date:
        raise HTTPException(status_code=400, detail="end_date は start_date 以降にしてください。")
    if not payload.title.strip():
        raise HTTPException(status_code=400, detail="title は必須です。")

    res = (
        supabase
        .table("portal_schedules")
        .insert({
            "start_date": payload.start_date,
            "end_date": payload.end_date,
            "assignee_ids": payload.assignee_ids or [],
            "assignee_names": payload.assignee_names or [],
            "title": payload.title.strip(),
            "description": payload.description.strip(),
            "created_by": user_id,
        })
        .execute()
    )

    return {"ok": True, "data": res.data}


@router.put("/calendar-schedules/{schedule_id}")
def update_calendar_schedule(
    schedule_id: str,
    payload: PortalScheduleBody,
    current_user: dict = Depends(require_admin_or_leader),
):
    if not payload.start_date:
        raise HTTPException(status_code=400, detail="start_date は必須です。")
    if not payload.end_date:
        raise HTTPException(status_code=400, detail="end_date は必須です。")
    if payload.end_date < payload.start_date:
        raise HTTPException(status_code=400, detail="end_date は start_date 以降にしてください。")
    if not payload.title.strip():
        raise HTTPException(status_code=400, detail="title は必須です。")

    res = (
        supabase
        .table("portal_schedules")
        .update({
            "start_date": payload.start_date,
            "end_date": payload.end_date,
            "assignee_ids": payload.assignee_ids or [],
            "assignee_names": payload.assignee_names or [],
            "title": payload.title.strip(),
            "description": payload.description.strip(),
            "updated_at": "now()",
        })
        .eq("id", schedule_id)
        .execute()
    )

    return {"ok": True, "data": res.data}


@router.delete("/calendar-schedules/{schedule_id}")
def delete_calendar_schedule(
    schedule_id: str,
    current_user: dict = Depends(require_admin_or_leader),
):
    res = (
        supabase
        .table("portal_schedules")
        .delete()
        .eq("id", schedule_id)
        .execute()
    )

    return {"ok": True, "data": res.data}

@router.get("/worklogs/today")
def get_today_worklogs(
    date: str | None = None,
    current_user: dict = Depends(require_admin_or_leader)
):
    from datetime import datetime

    target_date = date or datetime.today().strftime("%Y-%m-%d")

    worklog_res = (
        supabase
        .table("worklogs")
        .select("*")
        .eq("work_date", target_date)
        .order("start_time")
        .execute()
    )

    worklogs = worklog_res.data or []

    user_ids = list({
        row.get("user_id")
        for row in worklogs
        if row.get("user_id")
    })

    staff_map = {}

    if user_ids:
        staff_res = (
            supabase
            .table("staff_members")
            .select("id, staff_name, staff_code")
            .in_("id", user_ids)
            .execute()
        )

        for row in staff_res.data or []:
            staff_map[row.get("id")] = {
                "staff_name": row.get("staff_name") or "",
                "staff_code": row.get("staff_code") or "",
            }

    def calc_work_minutes(work_start_time: str | None, end_time: str | None, break_minutes: int | None):
        if not work_start_time or not end_time:
            return 0

        try:
            start_dt = datetime.strptime(work_start_time, "%H:%M")
            end_dt = datetime.strptime(end_time, "%H:%M")

            minutes = int((end_dt - start_dt).total_seconds() / 60)
            minutes -= int(break_minutes or 0)

            return max(minutes, 0)

        except Exception:
            return 0

    result = []

    for row in worklogs:
        staff_info = staff_map.get(row.get("user_id"), {})

        result.append({
            "id": row.get("id"),
            "user_id": row.get("user_id"),
            "staff_name": staff_info.get("staff_name", ""),
            "staff_code": staff_info.get("staff_code", ""),
            "work_date": row.get("work_date") or "",
            "property_name": row.get("property_name") or "",
            "room_name": row.get("room_name") or "",
            "work_start_time": row.get("work_start_time") or "",
            "start_time": row.get("start_time") or "",
            "end_time": row.get("end_time") or "",
            "break_minutes": row.get("break_minutes") or 0,
            "work_type": row.get("work_type") or "",
            "note": row.get("note") or "",
            "created_at": row.get("created_at") or "",
            "work_minutes": calc_work_minutes(
                row.get("work_start_time"),
                row.get("end_time"),
                row.get("break_minutes"),
            ),
        })

    return {
        "date": target_date,
        "worklogs": result,
    }
