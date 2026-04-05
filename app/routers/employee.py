from fastapi import APIRouter, Depends
from app.db import supabase
from app.services.auth_service import get_current_user_id

router = APIRouter(prefix="/api/employee", tags=["employee"])


@router.get("/me")
def get_me(user_id: str = Depends(get_current_user_id)):
    res = (
        supabase
        .table("users")
        .select("id, name, login_id, role")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )

    if not res.data:
        return {"user": None}

    return {"user": res.data[0]}


@router.get("/home")
def get_home(user_id: str = Depends(get_current_user_id)):
    user_res = (
        supabase
        .table("users")
        .select("id, assigned_properties")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )

    user_data = user_res.data[0] if user_res.data else {}

    return {
        "todayTaskCount": 0,
        "upcomingTaskCount": 0,
        "todayScheduleCount": 0,
        "unreadNoticeCount": 0,
        "assignedProperties": user_data.get("assigned_properties", []) or [],
    }


@router.get("/tasks")
def get_tasks(user_id: str = Depends(get_current_user_id)):
    return {
        "tasks": []
    }


@router.get("/schedule")
def get_schedule(user_id: str = Depends(get_current_user_id)):
    return {
        "schedules": []
    }


@router.post("/worklogs")
def create_worklog(payload: dict, user_id: str = Depends(get_current_user_id)):
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

    supabase.table("worklogs").insert(insert_data).execute()

    return {"message": "実働を登録しました。"}


@router.put("/settings/password")
def update_password(payload: dict, user_id: str = Depends(get_current_user_id)):
    current_password = payload.get("current_password")
    new_password = payload.get("new_password")

    user_res = (
        supabase
        .table("users")
        .select("id, password")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )

    if not user_res.data:
        return {"message": "ユーザーが見つかりません。"}

    user = user_res.data[0]

    if user.get("password") != current_password:
        return {"message": "現在のパスワードが正しくありません。"}

    (
        supabase
        .table("users")
        .update({"password": new_password})
        .eq("id", user_id)
        .execute()
    )

    return {"message": "パスワードを変更しました。"}
