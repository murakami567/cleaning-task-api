from datetime import date
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.db import supabase
from app.logger import get_logger
from app.services.auth_service import get_current_user_id

router = APIRouter(tags=["facility-trouble"])
logger = get_logger(__name__)


def _today() -> str:
    return date.today().isoformat()


def _get_staff_name(user_id: str) -> str:
    try:
        res = (
            supabase.table("staff_members")
            .select("staff_name")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0].get("staff_name") or ""
    except Exception as e:
        logger.error(f"facility trouble staff lookup failed: {e}", exc_info=True)
    return ""


@router.get("/facilities")
def get_facilities():
    try:
        res = (
            supabase.table("facilities")
            .select("*")
            .order("report_date", desc=True)
            .order("start_date", desc=True)
            .execute()
        )
    except Exception as e:
        logger.error(f"get_facilities failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="設備情報の取得に失敗しました。")
    return res.data or []


@router.post("/facilities/create")
def create_facility(
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
    payload = {
        "property_id": property_id,
        "property_name": property_name,
        "room_name": room_name,
        "assignee": assignee,
        "content": content,
        "start_date": start_date or report_date or _today(),
        "end_date": end_date,
        "status": status,
        "note": note,
        "report_date": report_date or start_date or _today(),
        "reporter_name": reporter_name or assignee or "",
        "photo_url": photo_url or "",
    }
    try:
        res = supabase.table("facilities").insert(payload).execute()
    except Exception as e:
        logger.error(f"create_facility failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"設備情報の保存に失敗しました: {str(e)}")
    if not res.data:
        raise HTTPException(status_code=500, detail="設備情報の保存に失敗しました。")
    return res.data[0]


@router.post("/facilities/update")
def update_facility(
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
    payload: dict[str, Any] = {}
    for key, value in {
        "property_id": property_id,
        "property_name": property_name,
        "room_name": room_name,
        "assignee": assignee,
        "content": content,
        "start_date": start_date,
        "end_date": end_date,
        "status": status,
        "note": note,
        "report_date": report_date,
        "reporter_name": reporter_name,
        "photo_url": photo_url,
    }.items():
        if value is not None:
            payload[key] = value

    if status == "対応完了" and not payload.get("end_date"):
        payload["end_date"] = _today()

    if not payload:
        raise HTTPException(status_code=400, detail="更新項目がありません。")

    try:
        res = supabase.table("facilities").update(payload).eq("id", facility_id).execute()
    except Exception as e:
        logger.error(f"update_facility failed: id={facility_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"設備情報の更新に失敗しました: {str(e)}")
    if not res.data:
        raise HTTPException(status_code=500, detail="設備情報の更新に失敗しました。")
    return res.data[0]


@router.post("/api/employee/facility-troubles")
def create_employee_facility_trouble(
    task_id: str = Body(...),
    property_name: str = Body(...),
    room_name: str = Body(...),
    task_date: str | None = Body(None),
    report_content: str = Body(...),
    photo_url: str = Body(...),
    user_id: str = Depends(get_current_user_id),
):
    reporter_name = _get_staff_name(user_id)
    report_date = task_date or _today()
    payload = {
        "property_id": None,
        "property_name": property_name,
        "room_name": room_name,
        "assignee": "",
        "content": report_content,
        "start_date": report_date,
        "end_date": None,
        "status": "保留",
        "note": "",
        "report_date": report_date,
        "reporter_name": reporter_name,
        "photo_url": photo_url,
        "source_task_id": task_id,
    }
    try:
        res = supabase.table("facilities").insert(payload).execute()
    except Exception as e:
        logger.error(f"create_employee_facility_trouble failed: user_id={user_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"設備トラブル報告の保存に失敗しました: {str(e)}")
    if not res.data:
        raise HTTPException(status_code=500, detail="設備トラブル報告の保存に失敗しました。")
    return {"message": "設備トラブルを報告しました。", "data": res.data[0]}
