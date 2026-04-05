from datetime import date
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.db import supabase
from app.services.auth_service import get_current_user_id

router = APIRouter(prefix="/api/employee", tags=["employee"])


@router.get("/me")
def get_me(user_id: str = Depends(get_current_user_id)):
    res = (
        supabase
        .table("staff_members")
        .select("id, staff_name, staff_code, role")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )

    if not res.data:
        return {"user": None}

    row = res.data[0]

    return {
        "user": {
            "id": row.get("id"),
            "name": row.get("staff_name"),
            "login_id": row.get("staff_code"),
            "role": row.get("role"),
        }
    }

@router.get("/home")
def get_home(user_id: str = Depends(get_current_user_id)):
    today = date.today().isoformat()

    user_res = (
        supabase
        .table("users")
        .select("id, assigned_properties")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )

    user_data = user_res.data[0] if user_res.data else {}
    assigned_properties = user_data.get("assigned_properties", []) or []

    today_task_res = (
        supabase
        .table("cleaning_tasks")
        .select("id")
        .eq("task_date", today)
        .contains("assigned_staff_ids", [user_id])
        .execute()
    )

    upcoming_task_res = (
        supabase
        .table("cleaning_tasks")
        .select("id")
        .gt("task_date", today)
        .contains("assigned_staff_ids", [user_id])
        .execute()
    )

    today_schedule_res = (
        supabase
        .table("shift_days")
        .select("id, shift_entries!inner(id, staff_id)")
        .eq("shift_date", today)
        .eq("shift_entries.staff_id", user_id)
        .execute()
    )

    return {
        "todayTaskCount": len(today_task_res.data or []),
        "upcomingTaskCount": len(upcoming_task_res.data or []),
        "todayScheduleCount": len(today_schedule_res.data or []),
        "unreadNoticeCount": 0,
        "assignedProperties": assigned_properties,
    }


@router.get("/tasks")
def get_tasks(user_id: str = Depends(get_current_user_id)):
    res = (
        supabase
        .table("cleaning_tasks")
        .select("*")
        .contains("assigned_staff_ids", [user_id])
        .order("task_date")
        .execute()
    )

    tasks = []
    for row in res.data or []:
        tasks.append({
            "id": row.get("id"),
            "title": f"清掃タスク {row.get('property_name', '')}",
            "propertyName": row.get("property_name", ""),
            "roomName": row.get("room_name", ""),
            "dueDate": row.get("task_date", ""),
            "status": normalize_task_status(row.get("status")),
            "note": row.get("note", ""),
        })

    return {"tasks": tasks}


@router.get("/schedule")
def get_schedule(user_id: str = Depends(get_current_user_id)):
    res = (
        supabase
        .table("shift_entries")
        .select("*, shift_days(*)")
        .eq("staff_id", user_id)
        .execute()
    )

    schedules = []
    for row in res.data or []:
        shift_day = row.get("shift_days") or {}

        schedules.append({
            "id": row.get("id"),
            "date": shift_day.get("shift_date", ""),
            "startTime": row.get("start_time") or "",
            "endTime": row.get("end_time") or "",
            "propertyName": row.get("assigned_area") or "",
            "roomName": "",
            "title": "勤務予定",
            "status": normalize_schedule_status(row.get("status")),
            "note": row.get("note") or "",
        })

    schedules.sort(key=lambda x: (x["date"], x["startTime"]))
    return {"schedules": schedules}


@router.post("/worklogs")
def create_worklog(
    payload: dict[str, Any] = Body(...),
    user_id: str = Depends(get_current_user_id),
):
    insert_data = {
        "user_id": user_id,
        "work_date": payload.get("work_date"),
        "property_name": payload.get("property_name"),
        "room_name": payload.get("room_name"),
        "start_time": payload.get("start_time"),
        "end_time": payload.get("end_time"),
        "break_minutes": payload.get("break_minutes", 0),
        "work_type": payload.get("work_type"),
        "note": payload.get("note"),
    }

    res = supabase.table("worklogs").insert(insert_data).execute()

    if not res.data:
        raise HTTPException(status_code=500, detail="実働登録に失敗しました。")

    return {"message": "実働を登録しました。", "data": res.data[0]}


@router.put("/settings/password")
def update_password(
    payload: dict[str, Any] = Body(...),
    user_id: str = Depends(get_current_user_id),
):
    current_password = payload.get("current_password")
    new_password = payload.get("new_password")

    if not current_password or not new_password:
        raise HTTPException(status_code=400, detail="パスワード情報が不足しています。")

    user_res = (
        supabase
        .table("users")
        .select("id, password")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )

    if not user_res.data:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません。")

    user = user_res.data[0]

    if user.get("password") != current_password:
        raise HTTPException(status_code=400, detail="現在のパスワードが正しくありません。")

    update_res = (
        supabase
        .table("users")
        .update({"password": new_password})
        .eq("id", user_id)
        .execute()
    )

    if not update_res.data:
        raise HTTPException(status_code=500, detail="パスワード変更に失敗しました。")

    return {"message": "パスワードを変更しました。"}


def normalize_task_status(status: str | None) -> str:
    if status in ["完了", "清掃完了", "completed"]:
        return "completed"
    if status in ["対応中", "清掃中", "in_progress"]:
        return "in_progress"
    return "pending"


def normalize_schedule_status(status: str | None) -> str:
    if status in ["出勤", "予定", "scheduled"]:
        return "scheduled"
    if status in ["勤務中", "対応中", "in_progress"]:
        return "in_progress"
    if status in ["完了", "退勤", "completed"]:
        return "completed"
    return "scheduled"
