from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.db import supabase
from app.logger import get_logger
from app.services.auth_service import require_admin_or_leader

router = APIRouter(prefix="/mate-cartes", tags=["mate-cartes"])
logger = get_logger(__name__)


def _get_current_staff(current_user: dict) -> dict[str, Any]:
    user_id = current_user.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="認証情報が不正です。")
    try:
        res = (
            supabase.table("staff_members")
            .select("id, staff_name, staff_code, role")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )
    except Exception as e:
        logger.error(f"mate current staff lookup failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="ログインスタッフ取得に失敗しました。")
    if not res.data:
        raise HTTPException(status_code=401, detail="ログインスタッフが見つかりません。")
    return res.data[0]


@router.get("/targets")
def get_mate_targets(current_user: dict = Depends(require_admin_or_leader)):
    try:
        res = (
            supabase.table("staff_members")
            .select("id, staff_code, staff_name, role, is_active, sort_order")
            .in_("role", ["staff", "checker"])
            .eq("is_active", True)
            .order("sort_order")
            .order("staff_name")
            .execute()
        )
    except Exception as e:
        logger.error(f"get_mate_targets failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="対象スタッフ取得に失敗しました。")
    return res.data or []


@router.get("/properties")
def get_mate_properties(current_user: dict = Depends(require_admin_or_leader)):
    try:
        res = (
            supabase.table("properties")
            .select("id, property_name, property_code, sort_order, is_active")
            .eq("is_active", True)
            .order("sort_order")
            .order("property_name")
            .execute()
        )
    except Exception as e:
        logger.error(f"get_mate_properties failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="物件取得に失敗しました。")
    return res.data or []


@router.get("/{staff_id}")
def get_mate_cartes(staff_id: str, current_user: dict = Depends(require_admin_or_leader)):
    try:
        res = (
            supabase.table("mate_cartes")
            .select("*")
            .eq("target_staff_id", staff_id)
            .order("record_date", desc=True)
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as e:
        logger.error(f"get_mate_cartes failed: staff_id={staff_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="メイトカルテ取得に失敗しました。")
    return res.data or []


@router.get("/{staff_id}/cleaning-summary")
def get_cleaning_summary(
    staff_id: str,
    record_date: str,
    current_user: dict = Depends(require_admin_or_leader),
):
    if not record_date:
        raise HTTPException(status_code=400, detail="record_date is required")
    try:
        res = (
            supabase.table("cleaning_tasks")
            .select("id, property_name, room_name, task_date, status, assigned_staff_ids, assigned_staff_id, cleaning_started_at, cleaning_completed_at")
            .eq("task_date", record_date)
            .execute()
        )
    except Exception as e:
        logger.error(f"get_cleaning_summary failed: staff_id={staff_id} date={record_date} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="清掃時間取得に失敗しました。")

    tasks = []
    started_values: list[str] = []
    completed_values: list[str] = []
    property_names: list[str] = []

    for row in res.data or []:
        ids = row.get("assigned_staff_ids") if isinstance(row.get("assigned_staff_ids"), list) else []
        single_id = row.get("assigned_staff_id")
        if staff_id not in ids and staff_id != single_id:
            continue
        tasks.append(row)
        property_name = row.get("property_name")
        if property_name and property_name not in property_names:
            property_names.append(property_name)
        started = row.get("cleaning_started_at")
        completed = row.get("cleaning_completed_at")
        if started:
            started_values.append(started)
        if completed:
            completed_values.append(completed)

    start_at = min(started_values) if started_values else None
    end_at = max(completed_values) if completed_values else None

    return {
        "staff_id": staff_id,
        "record_date": record_date,
        "task_count": len(tasks),
        "property_names": property_names,
        "cleaning_start_at": start_at,
        "cleaning_end_at": end_at,
        "tasks": tasks,
    }


@router.post("")
def create_mate_carte(
    target_staff_id: str = Body(...),
    record_date: str = Body(...),
    property_ids: list[str] = Body(default=[]),
    property_names: list[str] = Body(default=[]),
    cleaning_start_at: str | None = Body(None),
    cleaning_end_at: str | None = Body(None),
    guidance_category: str = Body(...),
    good_points: str = Body(""),
    correction_points: str = Body(""),
    handover_notes: str = Body(""),
    current_user: dict = Depends(require_admin_or_leader),
):
    if guidance_category not in ["清掃全般", "倉庫作業", "その他作業"]:
        raise HTTPException(status_code=400, detail="指導内容が不正です。")

    instructor = _get_current_staff(current_user)

    try:
        target_res = (
            supabase.table("staff_members")
            .select("id, staff_name, staff_code, role")
            .eq("id", target_staff_id)
            .limit(1)
            .execute()
        )
    except Exception as e:
        logger.error(f"mate target lookup failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="対象スタッフ取得に失敗しました。")

    if not target_res.data:
        raise HTTPException(status_code=404, detail="対象スタッフが見つかりません。")

    target = target_res.data[0]
    now = datetime.now(timezone.utc).isoformat()

    payload = {
        "target_staff_id": target_staff_id,
        "target_staff_name": target.get("staff_name") or "",
        "record_date": record_date,
        "instructor_id": instructor.get("id"),
        "instructor_name": instructor.get("staff_name") or "",
        "property_ids": property_ids or [],
        "property_names": property_names or [],
        "cleaning_start_at": cleaning_start_at,
        "cleaning_end_at": cleaning_end_at,
        "guidance_category": guidance_category,
        "good_points": good_points or "",
        "correction_points": correction_points or "",
        "handover_notes": handover_notes or "",
        "created_at": now,
        "updated_at": now,
    }

    try:
        res = supabase.table("mate_cartes").insert(payload).execute()
    except Exception as e:
        logger.error(f"create_mate_carte failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="メイトカルテ保存に失敗しました。")

    if not res.data:
        raise HTTPException(status_code=500, detail="メイトカルテ保存に失敗しました。")

    logger.info(f"create_mate_carte: target={target_staff_id} date={record_date}")
    return res.data[0]
