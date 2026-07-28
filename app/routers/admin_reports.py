from datetime import date
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel

from app.db import supabase
from app.logger import get_logger
from app.services.auth_service import get_current_user_id, require_admin_or_leader

logger = get_logger(__name__)

admin_router = APIRouter(prefix="/api/admin-portal", tags=["admin-reports"])
employee_router = APIRouter(prefix="/api/employee", tags=["employee-reports"])


class WorklogBody(BaseModel):
    work_date: str
    property_name: str
    room_name: str
    work_start_time: str = ""
    start_time: str
    end_time: str
    break_minutes: int = 0
    work_type: str = "cleaning"
    note: str = ""


class LostItemBody(BaseModel):
    task_id: str | None = None
    task_date: str | None = None
    property_name: str = ""
    room_name: str = ""
    item_description: str
    photo_url: str = ""


class FacilityReportBody(BaseModel):
    task_id: str | None = None
    task_date: str | None = None
    property_name: str = ""
    room_name: str = ""
    description: str
    photo_url: str = ""


def _minutes_between(start_time: str, end_time: str, break_minutes: int) -> int:
    try:
        sh, sm = [int(x) for x in start_time.split(":")[:2]]
        eh, em = [int(x) for x in end_time.split(":")[:2]]
    except Exception:
        return 0
    minutes = (eh * 60 + em) - (sh * 60 + sm) - int(break_minutes or 0)
    return max(minutes, 0)


def _staff_map(ids: list[str]) -> dict[str, dict[str, Any]]:
    clean_ids = [x for x in dict.fromkeys(ids) if x]
    if not clean_ids:
        return {}
    try:
        res = (
            supabase.table("staff_members")
            .select("id, staff_name, staff_code")
            .in_("id", clean_ids)
            .execute()
        )
    except Exception as e:
        logger.warning(f"staff lookup skipped: {e}")
        return {}
    return {str(row.get("id")): row for row in (res.data or [])}


@employee_router.post("/worklogs")
def create_employee_worklog(
    payload: WorklogBody,
    user_id: str = Depends(get_current_user_id),
):
    if not payload.work_date:
        raise HTTPException(status_code=400, detail="work_date is required")
    if not payload.property_name or not payload.room_name:
        raise HTTPException(status_code=400, detail="property_name and room_name are required")
    if not payload.start_time or not payload.end_time:
        raise HTTPException(status_code=400, detail="start_time and end_time are required")

    staff = _staff_map([user_id]).get(user_id, {})
    work_minutes = _minutes_between(
        payload.start_time,
        payload.end_time,
        payload.break_minutes,
    )

    row = {
        "user_id": user_id,
        "staff_name": staff.get("staff_name") or "",
        "staff_code": staff.get("staff_code") or "",
        "work_date": payload.work_date,
        "property_name": payload.property_name,
        "room_name": payload.room_name,
        "work_start_time": payload.work_start_time or payload.start_time,
        "start_time": payload.start_time,
        "end_time": payload.end_time,
        "break_minutes": int(payload.break_minutes or 0),
        "work_type": payload.work_type or "cleaning",
        "note": payload.note or "",
        "work_minutes": work_minutes,
    }

    try:
        res = supabase.table("work_logs").insert(row).execute()
    except Exception as e:
        logger.error(f"create_employee_worklog failed: user_id={user_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="実働報告の保存に失敗しました。")

    return {"ok": True, "data": res.data}


@employee_router.post("/lost-items")
def create_lost_item(
    payload: LostItemBody,
    user_id: str = Depends(get_current_user_id),
):
    if not payload.item_description.strip():
        raise HTTPException(status_code=400, detail="item_description is required")

    staff = _staff_map([user_id]).get(user_id, {})
    row = {
        "task_id": payload.task_id,
        "task_date": payload.task_date or date.today().isoformat(),
        "property_name": payload.property_name,
        "room_name": payload.room_name,
        "item_description": payload.item_description.strip(),
        "photo_url": payload.photo_url or "",
        "reported_by": user_id,
        "reported_by_name": staff.get("staff_name") or "",
    }

    try:
        res = supabase.table("lost_items").insert(row).execute()
    except Exception as e:
        logger.error(f"create_lost_item failed: user_id={user_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="忘れ物報告の保存に失敗しました。")

    return {"ok": True, "data": res.data}


@employee_router.post("/facility-reports")
def create_facility_report(
    payload: FacilityReportBody,
    user_id: str = Depends(get_current_user_id),
):
    if not payload.description.strip():
        raise HTTPException(status_code=400, detail="description is required")
    if not payload.photo_url:
        raise HTTPException(status_code=400, detail="photo_url is required")

    staff = _staff_map([user_id]).get(user_id, {})
    row = {
        "task_id": payload.task_id,
        "task_date": payload.task_date or date.today().isoformat(),
        "property_name": payload.property_name,
        "room_name": payload.room_name,
        "description": payload.description.strip(),
        "photo_url": payload.photo_url,
        "reported_by": user_id,
        "reported_by_name": staff.get("staff_name") or "",
    }

    try:
        res = supabase.table("facility_reports").insert(row).execute()
    except Exception as e:
        logger.error(f"create_facility_report failed: user_id={user_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="設備トラブル報告の保存に失敗しました。")

    return {"ok": True, "data": res.data}


@admin_router.get("/worklogs/today")
def get_admin_worklogs(
    date: str = Query(default_factory=lambda: date.today().isoformat()),
    current_user: dict = Depends(require_admin_or_leader),
):
    try:
        res = (
            supabase.table("work_logs")
            .select("*")
            .eq("work_date", date)
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as e:
        logger.error(f"get_admin_worklogs failed: date={date} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="実働報告の取得に失敗しました。")

    rows = res.data or []
    staff_by_id = _staff_map([str(row.get("user_id") or "") for row in rows])

    worklogs = []
    for row in rows:
        sid = str(row.get("user_id") or "")
        staff = staff_by_id.get(sid, {})
        break_minutes = int(row.get("break_minutes") or 0)
        work_minutes = row.get("work_minutes")
        if work_minutes is None:
            work_minutes = _minutes_between(
                row.get("start_time") or "",
                row.get("end_time") or "",
                break_minutes,
            )
        worklogs.append({
            "id": row.get("id"),
            "user_id": sid,
            "staff_name": row.get("staff_name") or staff.get("staff_name") or "",
            "staff_code": row.get("staff_code") or staff.get("staff_code") or "",
            "work_date": row.get("work_date") or date,
            "property_name": row.get("property_name") or "",
            "room_name": row.get("room_name") or "",
            "work_start_time": row.get("work_start_time") or row.get("start_time") or "",
            "start_time": row.get("start_time") or "",
            "end_time": row.get("end_time") or "",
            "break_minutes": break_minutes,
            "work_type": row.get("work_type") or "",
            "note": row.get("note") or "",
            "created_at": row.get("created_at") or "",
            "work_minutes": int(work_minutes or 0),
        })

    logger.info(f"get_admin_worklogs: date={date} count={len(worklogs)} user={current_user.get('user_id')}")
    return {"worklogs": worklogs}


@admin_router.get("/lost-items")
def get_admin_lost_items(current_user: dict = Depends(require_admin_or_leader)):
    try:
        res = (
            supabase.table("lost_items")
            .select("*")
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as e:
        logger.error(f"get_admin_lost_items failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="忘れ物一覧の取得に失敗しました。")

    rows = res.data or []
    staff_by_id = _staff_map([str(row.get("reported_by") or "") for row in rows])

    items = []
    for row in rows:
        reporter_id = str(row.get("reported_by") or "")
        staff = staff_by_id.get(reporter_id, {})
        items.append({
            "id": row.get("id"),
            "task_id": row.get("task_id"),
            "task_date": row.get("task_date") or "",
            "property_name": row.get("property_name") or "",
            "room_name": row.get("room_name") or "",
            "item_description": row.get("item_description") or "",
            "photo_url": row.get("photo_url") or "",
            "reported_by": reporter_id,
            "reported_by_name": row.get("reported_by_name") or staff.get("staff_name") or "",
            "created_at": row.get("created_at") or "",
        })

    logger.info(f"get_admin_lost_items: count={len(items)} user={current_user.get('user_id')}")
    return {"items": items}


@admin_router.get("/facility-reports")
def get_admin_facility_reports(current_user: dict = Depends(require_admin_or_leader)):
    try:
        res = (
            supabase.table("facility_reports")
            .select("*")
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as e:
        logger.error(f"get_admin_facility_reports failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="設備報告一覧の取得に失敗しました。")

    rows = res.data or []
    staff_by_id = _staff_map([str(row.get("reported_by") or "") for row in rows])

    items = []
    for row in rows:
        reporter_id = str(row.get("reported_by") or "")
        staff = staff_by_id.get(reporter_id, {})
        items.append({
            "id": row.get("id"),
            "task_id": row.get("task_id"),
            "task_date": row.get("task_date") or "",
            "property_name": row.get("property_name") or "",
            "room_name": row.get("room_name") or "",
            "description": row.get("description") or "",
            "photo_url": row.get("photo_url") or "",
            "reported_by": reporter_id,
            "reported_by_name": row.get("reported_by_name") or staff.get("staff_name") or "",
            "created_at": row.get("created_at") or "",
        })

    logger.info(f"get_admin_facility_reports: count={len(items)} user={current_user.get('user_id')}")
    return {"items": items}
