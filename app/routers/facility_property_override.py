from datetime import date
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.db import supabase
from app.logger import get_logger
from app.services.auth_service import get_current_user_id

router = APIRouter(tags=["facility-property-override"])
logger = get_logger(__name__)


def _today() -> str:
    return date.today().isoformat()


def _normalize_status(status: str | None) -> str:
    if status in ["完了", "対応完了", "対応済み"]:
        return "対応済み"
    if status == "対応中":
        return "対応中"
    return "保留"


def _property_id_by_name(property_name: str | None) -> str | None:
    if not property_name:
        return None
    try:
        res = (
            supabase.table("properties")
            .select("id")
            .eq("property_name", property_name.strip())
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0].get("id")
    except Exception as e:
        logger.warning(f"property id lookup failed: {property_name} {e}")
    return None


def _staff_name(user_id: str) -> str:
    try:
        res = supabase.table("staff_members").select("staff_name").eq("id", user_id).limit(1).execute()
        if res.data:
            return res.data[0].get("staff_name") or ""
    except Exception as e:
        logger.warning(f"staff name lookup failed: {user_id} {e}")
    return ""


@router.get("/facilities")
def get_facilities_with_property_id():
    try:
        res = (
            supabase.table("facilities")
            .select("*")
            .order("report_date", desc=True)
            .order("start_date", desc=True)
            .execute()
        )
    except Exception as e:
        logger.error(f"get_facilities_with_property_id failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="設備情報の取得に失敗しました。")

    rows = res.data or []
    for row in rows:
        row["status"] = _normalize_status(row.get("status"))
        if not row.get("property_id"):
            row["property_id"] = _property_id_by_name(row.get("property_name"))
    return rows


@router.post("/facilities/create")
def create_facility_with_property_id(
    property_id: str | None = Body(None),
    property_name: str = Body(...),
    room_name: str = Body(...),
    assignee: str = Body(""),
    content: str = Body(...),
    start_date: str | None = Body(None),
    end_date: str | None = Body(None),
    status: str = Body("保留"),
    note: str = Body(""),
    report_date: str | None = Body(None),
    reporter_name: str | None = Body(None),
    photo_url: str | None = Body(None),
):
    normalized_status = _normalize_status(status)
    payload = {
        "property_id": property_id or _property_id_by_name(property_name),
        "property_name": property_name,
        "room_name": room_name,
        "assignee": assignee,
        "content": content,
        "start_date": start_date or report_date or _today(),
        "end_date": end_date or (_today() if normalized_status == "対応済み" else None),
        "status": normalized_status,
        "note": note,
        "report_date": report_date or start_date or _today(),
        "reporter_name": reporter_name or assignee or "",
        "photo_url": photo_url or "",
    }
    try:
        res = supabase.table("facilities").insert(payload).execute()
    except Exception as e:
        logger.error(f"create_facility_with_property_id failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"設備情報の保存に失敗しました: {str(e)}")
    return res.data[0] if res.data else payload


@router.post("/facilities/update")
def update_facility_with_property_id(
    facility_id: str = Body(...),
    property_id: str | None = Body(None),
    property_name: str | None = Body(None),
    room_name: str | None = Body(None),
    assignee: str | None = Body(None),
    content: str | None = Body(None),
    start_date: str | None = Body(None),
    end_date: str | None = Body(None),
    status: str | None = Body(None),
    note: str | None = Body(None),
    report_date: str | None = Body(None),
    reporter_name: str | None = Body(None),
    photo_url: str | None = Body(None),
):
    normalized_status = _normalize_status(status) if status is not None else None
    payload: dict[str, Any] = {}
    resolved_property_id = property_id or _property_id_by_name(property_name)
    for key, value in {
        "property_id": resolved_property_id,
        "property_name": property_name,
        "room_name": room_name,
        "assignee": assignee,
        "content": content,
        "start_date": start_date,
        "end_date": end_date,
        "status": normalized_status,
        "note": note,
        "report_date": report_date,
        "reporter_name": reporter_name,
        "photo_url": photo_url,
    }.items():
        if value is not None:
            payload[key] = value
    if normalized_status == "対応済み" and not payload.get("end_date"):
        payload["end_date"] = _today()
    try:
        res = supabase.table("facilities").update(payload).eq("id", facility_id).execute()
    except Exception as e:
        logger.error(f"update_facility_with_property_id failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"設備情報の更新に失敗しました: {str(e)}")
    return res.data[0] if res.data else payload


@router.post("/api/employee/facility-troubles")
def create_employee_facility_trouble_with_property_id(
    task_id: str = Body(...),
    property_name: str = Body(...),
    room_name: str = Body(...),
    task_date: str | None = Body(None),
    report_content: str = Body(...),
    photo_url: str = Body(...),
    user_id: str = Depends(get_current_user_id),
):
    report_date = task_date or _today()
    payload = {
        "property_id": _property_id_by_name(property_name),
        "property_name": property_name,
        "room_name": room_name,
        "assignee": "",
        "content": report_content,
        "start_date": report_date,
        "end_date": None,
        "status": "保留",
        "note": "",
        "report_date": report_date,
        "reporter_name": _staff_name(user_id),
        "photo_url": photo_url,
        "source_task_id": task_id,
    }
    try:
        res = supabase.table("facilities").insert(payload).execute()
    except Exception as e:
        logger.error(f"create employee facility trouble failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"設備トラブル報告の保存に失敗しました: {str(e)}")
    return {"message": "設備トラブルを報告しました。", "data": res.data[0] if res.data else payload}
