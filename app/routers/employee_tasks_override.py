from datetime import date
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.db import supabase
from app.logger import get_logger
from app.routers.tasks import _auto_progress_started_tasks
from app.services.auth_service import get_current_user_id

router = APIRouter(prefix="/api/employee", tags=["employee"])
logger = get_logger(__name__)

CHECK_STATUS_VALUES = {"未着手", "チェック完了", "CXL"}


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

    # 部屋マスタのアーリーCI / レイトCO料金をタスク表示へ反映する。
    fee_map: dict[tuple[str, str], dict[str, int]] = {}
    try:
        properties_res = supabase.table("properties").select("id,property_name").execute()
        property_names = {str(row.get("id")): str(row.get("property_name") or "") for row in (properties_res.data or [])}
        rooms_res = supabase.table("rooms").select("property_id,room_name,early_checkin_fee,late_checkout_fee").execute()
        for room in rooms_res.data or []:
            property_name = property_names.get(str(room.get("property_id")), "")
            room_name = str(room.get("room_name") or "")
            if property_name and room_name:
                fee_map[(property_name, room_name)] = {
                    "early_checkin_fee": int(room.get("early_checkin_fee") or 0),
                    "late_checkout_fee": int(room.get("late_checkout_fee") or 0),
                }
    except Exception as e:
        logger.warning(f"room fee lookup failed: {e}")

    cleaning_tasks = [map_cleaning_task(row, "cleaning", fee_map) for row in (cleaning_res.data or [])]
    check_tasks = [map_cleaning_task(row, "check", fee_map) for row in ((check_res.data if check_res else []) or [])]
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


@router.post("/check-tasks/update")
def update_check_task_status(
    task_id: str = Body(...),
    status: str = Body(...),
    note: str | None = Body(None),
    user_id: str = Depends(get_current_user_id),
):
    normalized_status = str(status or "").strip()
    if normalized_status not in CHECK_STATUS_VALUES:
        raise HTTPException(
            status_code=400,
            detail="チェックタスクのステータスは 未着手・チェック完了・CXL のみ指定できます。",
        )

    try:
        staff_res = (
            supabase.table("staff_members")
            .select("staff_name")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )
        staff_name = staff_res.data[0].get("staff_name") if staff_res.data else ""
        if not staff_name:
            raise HTTPException(status_code=403, detail="チェッカー情報を確認できません。")

        task_res = (
            supabase.table("cleaning_tasks")
            .select("id, checker_name")
            .eq("id", task_id)
            .limit(1)
            .execute()
        )
        if not task_res.data:
            raise HTTPException(status_code=404, detail="チェックタスクが見つかりません。")
        if (task_res.data[0].get("checker_name") or "") != staff_name:
            raise HTTPException(status_code=403, detail="このチェックタスクを更新する権限がありません。")

        payload: dict[str, Any] = {"status": normalized_status, "cleaning_started_at": None}
        if note is not None:
            payload["note"] = note

        update_res = (
            supabase.table("cleaning_tasks")
            .update(payload)
            .eq("id", task_id)
            .execute()
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"update_check_task_status failed: user_id={user_id} task_id={task_id} {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="チェックタスクの更新に失敗しました。")

    logger.info(
        f"update_check_task_status: user_id={user_id} task_id={task_id} status={normalized_status}"
    )
    return {"ok": True, "task_id": task_id, "updated": payload, "data": update_res.data}


def map_cleaning_task(row: dict[str, Any], task_kind: str, fee_map: dict[tuple[str, str], dict[str, int]] | None = None):
    property_name = row.get("property_name") or ""
    room_name = row.get("room_name") or ""
    next_guest_count = row.get("next_guest_count") or 0
    next_stay_nights = row.get("next_stay_nights") or 0

    room_fees = (fee_map or {}).get((property_name, room_name), {})

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
        "rateCi": room_fees.get("early_checkin_fee", 0),
        "rateCo": room_fees.get("late_checkout_fee", 0),
        "earlyCheckinTime": row.get("early_checkin_time") or "",
        "lateCheckoutTime": row.get("late_checkout_time") or "",
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
