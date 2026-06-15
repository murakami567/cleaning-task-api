from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.db import supabase
from app.logger import get_logger
from app.routers.tasks import _auto_progress_started_tasks
from app.services.auth_service import get_current_user_id

router = APIRouter(prefix="/api/employee", tags=["employee"])
logger = get_logger(__name__)


@router.get("/tasks")
def get_tasks_with_checklist(user_id: str = Depends(get_current_user_id)):
    today = date.today().isoformat()
    _auto_progress_started_tasks()

    try:
        staff_res = (
            supabase.table("staff_members")
            .select("id, staff_name")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )
        staff_name = staff_res.data[0].get("staff_name") if staff_res.data else ""

        cleaning_res = (
            supabase.table("cleaning_tasks")
            .select("*")
            .contains("assigned_staff_ids", [user_id])
            .eq("task_date", today)
            .order("task_date")
            .execute()
        )

        check_res = (
            supabase.table("cleaning_tasks")
            .select("*")
            .eq("checker_name", staff_name)
            .eq("task_date", today)
            .order("task_date")
            .execute()
        ) if staff_name else None

        other_res = (
            supabase.table("non_cleaning_tasks")
            .select("*")
            .contains("assignee_ids", [user_id])
            .eq("task_date", today)
            .order("task_date")
            .execute()
        )
    except Exception as e:
        logger.error(f"get_tasks_with_checklist failed: user_id={user_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="タスク情報の取得に失敗しました。")

    cleaning_tasks = [map_cleaning_task(row, "cleaning") for row in (cleaning_res.data or [])]
    check_tasks = [map_cleaning_task(row, "check") for row in ((check_res.data if check_res else []) or [])]
    other_tasks = [map_other_task(row) for row in (other_res.data or [])]

    logger.info(
        f"get_tasks_with_checklist: user_id={user_id} cleaning={len(cleaning_tasks)} check={len(check_tasks)} other={len(other_tasks)}"
    )

    return {
        "otherTasks": other_tasks,
        "cleaningTasks": cleaning_tasks,
        "checkTasks": check_tasks,
        "summary": {
            "cleaningTodayCount": count_today(cleaning_tasks),
            "checkTodayCount": count_today(check_tasks),
        },
    }


def map_cleaning_task(row: dict[str, Any], task_kind: str):
    property_name = row.get("property_name") or ""
    room_name = row.get("room_name") or ""
    next_guest_count = row.get("next_guest_count") or 0
    next_stay_nights = row.get("next_stay_nights") or 0

    checklist = row.get("checklist")
    if not isinstance(checklist, dict):
        checklist = {}

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
        "checklist": checklist,
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
        "checklist": {},
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
    if status in ["チェック完了", "check_completed"]:
        return "check_completed"
    if status in ["完了", "清掃完了", "completed"]:
        return "completed"
    if status in ["CXL", "cancelled", "キャンセル"]:
        return "cancelled"
    if status in ["清掃開始", "started"]:
        return "started"
    if status in ["対応中", "清掃中", "チェック中", "in_progress"]:
        return "in_progress"
    return "pending"
