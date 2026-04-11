from collections import defaultdict
from datetime import date
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query

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

    staff_res = (
        supabase
        .table("staff_members")
        .select("id, staff_name")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )

    staff_name = ""
    if staff_res.data:
        staff_name = staff_res.data[0].get("staff_name") or ""

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

    today_check_res = (
        supabase
        .table("cleaning_tasks")
        .select("id")
        .eq("task_date", today)
        .eq("checker_name", staff_name)
        .execute()
    ) if staff_name else None

    message_res = (
        supabase
        .table("portal_messages")
        .select("id, message, target_date, updated_at")
        .eq("target_date", today)
        .order("updated_at", desc=True)
        .execute()
    )

    today_messages = message_res.data or []

    return {
        "todayTaskCount": len(today_task_res.data or []),
        "upcomingTaskCount": len(upcoming_task_res.data or []),
        "todayScheduleCount": len(today_schedule_res.data or []),
        "unreadNoticeCount": 0,
        "todayCheckCount": len((today_check_res.data if today_check_res else []) or []),
        "assignedProperties": [],
        "todayMessages": today_messages,
    }

@router.get("/tasks")
def get_tasks(user_id: str = Depends(get_current_user_id)):
    today = date.today().isoformat()

    staff_res = (
        supabase
        .table("staff_members")
        .select("id, staff_name")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )

    staff_name = ""
    if staff_res.data:
        staff_name = staff_res.data[0].get("staff_name") or ""

    cleaning_res = (
        supabase
        .table("cleaning_tasks")
        .select("*")
        .contains("assigned_staff_ids", [user_id])
        .eq("task_date", today)
        .order("task_date")
        .execute()
    )

    if staff_name:
        check_res = (
            supabase
            .table("cleaning_tasks")
            .select("*")
            .eq("checker_name", staff_name)
            .eq("task_date", today)
            .order("task_date")
            .execute()
        )
    else:
        check_res = None

    other_res = (
        supabase
        .table("non_cleaning_tasks")
        .select("*")
        .contains("assignee_ids", [user_id])
        .eq("task_date", today)
        .order("task_date")
        .execute()
    )

    cleaning_tasks = [
        map_cleaning_task(row, "cleaning")
        for row in (cleaning_res.data or [])
    ]
    check_tasks = [
        map_cleaning_task(row, "check")
        for row in ((check_res.data if check_res else []) or [])
    ]
    other_tasks = [
        map_other_task(row)
        for row in (other_res.data or [])
    ]

    return {
        "otherTasks": other_tasks,
        "cleaningTasks": cleaning_tasks,
        "checkTasks": check_tasks,
        "summary": {
            "cleaningTodayCount": count_today(cleaning_tasks),
            "checkTodayCount": count_today(check_tasks),
        },
    }


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


@router.get("/schedule-calendar")
def get_schedule_calendar(
    month: str = Query(..., description="YYYY-MM"),
    user_id: str = Depends(get_current_user_id),
):
    from datetime import datetime

    try:
        target_month = datetime.strptime(month, "%Y-%m")
    except ValueError:
        raise HTTPException(status_code=400, detail="month は YYYY-MM 形式で指定してください。")

    month_start = target_month.date().replace(day=1)

    if target_month.month == 12:
        next_month_start = date(target_month.year + 1, 1, 1)
    else:
        next_month_start = date(target_month.year, target_month.month + 1, 1)

    res = (
        supabase
        .table("cleaning_tasks")
        .select("*")
        .contains("assigned_staff_ids", [user_id])
        .gte("task_date", month_start.isoformat())
        .lt("task_date", next_month_start.isoformat())
        .order("task_date")
        .execute()
    )

    rows = res.data or []

    calendar_map = defaultdict(lambda: {
        "cleaningCount": 0,
        "inspectionCount": 0,
        "propertyCounts": defaultdict(int),
    })

    for row in rows:
        task_date = row.get("task_date")
        if not task_date:
            continue

        property_name = row.get("property_name") or ""
        note = row.get("note") or ""
        title = row.get("title") or ""

        text_for_judge = f"{title} {note} {property_name}"
        is_inspection = (
            "インスペクション" in text_for_judge
            or "点検" in text_for_judge
            or "検品" in text_for_judge
        )

        if is_inspection:
            calendar_map[task_date]["inspectionCount"] += 1
        else:
            calendar_map[task_date]["cleaningCount"] += 1

        if property_name:
            calendar_map[task_date]["propertyCounts"][property_name] += 1

    result = []
    for task_date, value in sorted(calendar_map.items()):
        result.append({
            "date": task_date,
            "cleaningCount": value["cleaningCount"],
            "inspectionCount": value["inspectionCount"],
            "propertyCounts": dict(value["propertyCounts"]),
            "totalCount": value["cleaningCount"] + value["inspectionCount"],
        })

    return {
        "month": month,
        "days": result,
    }


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
        "work_start_time": payload.get("work_start_time"),
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
        .table("staff_members")
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
        .table("staff_members")
        .update({"password": new_password})
        .eq("id", user_id)
        .execute()
    )

    if not update_res.data:
        raise HTTPException(status_code=500, detail="パスワード変更に失敗しました。")

    return {"message": "パスワードを変更しました。"}


def map_cleaning_task(row: dict[str, Any], task_kind: str):
    property_name = row.get("property_name") or ""
    room_name = row.get("room_name") or ""
    next_guest_count = row.get("next_guest_count") or 0
    next_stay_nights = row.get("next_stay_nights") or 0

    return {
        "id": row.get("id"),
        "taskKind": task_kind,
        "title": f"{'チェックタスク' if task_kind == 'check' else '清掃タスク'} {property_name}",
        "propertyName": property_name,
        "roomName": room_name,
        "status": normalize_task_status(row.get("status")),
        "date": row.get("task_date") or "",
        "deadline": row.get("next_checkin_date") or row.get("task_date") or "",
        "dueDate": row.get("task_date") or "",
        "note": row.get("note") or "",
        "assigneeName": first_list_value(row.get("assigned_staff_names")) or row.get("assigned_staff_name") or "",
        "checkerName": row.get("checker_name") or "",
        "rateCi": row.get("early_checkin_fee") or "",
        "rateCo": row.get("late_checkout_fee") or "",
        "towelCount": calc_towel_display(property_name, next_guest_count, next_stay_nights),
    }


def map_other_task(row: dict[str, Any]):
    return {
        "id": row.get("id"),
        "taskKind": "other",
        "title": row.get("title") or "その他タスク",
        "propertyName": "",
        "roomName": "",
        "status": normalize_task_status(row.get("status")),
        "date": row.get("task_date") or "",
        "deadline": row.get("deadline") or row.get("task_date") or "",
        "dueDate": row.get("task_date") or "",
        "note": row.get("note") or "",
        "assigneeName": first_list_value(row.get("assignee_names")) or row.get("assignee_name") or "",
        "checkerName": row.get("checker_name") or "",
        "rateCi": "",
        "rateCo": "",
        "towelCount": "",
    }


def first_list_value(value: Any):
    if isinstance(value, list) and len(value) > 0:
        return value[0]
    return ""


def calc_towel_display(property_name: str, next_guest_count: int, next_stay_nights: int):
    if property_name in ["FFFホテル", "やなぎ橋"]:
        return ""

    guests = int(next_guest_count or 0)
    nights = int(next_stay_nights or 0)

    if guests <= 0 or nights <= 0:
        return "-"
    if nights >= 8:
        return guests * 3
    if nights >= 3:
        return guests * 2
    return guests


def count_today(tasks: list[dict[str, Any]]):
    today = date.today().isoformat()
    return len([t for t in tasks if t.get("date") == today])


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
