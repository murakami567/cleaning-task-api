from datetime import date
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.db import supabase
from app.logger import get_logger
from app.services.auth_service import get_current_user_id, require_operational_write

router = APIRouter(tags=["facility-trouble"])
logger = get_logger(__name__)

FACILITY_STATUSES = ["保留", "対応中", "対応済み"]
FACILITY_TASK_CATEGORY = "OTHER"


def _today() -> str:
    return date.today().isoformat()


def _normalize_status(status: str | None) -> str:
    if status in ["完了", "対応完了", "対応済み"]:
        return "対応済み"
    if status == "対応中":
        return "対応中"
    return "保留"


def _facility_task_status(status: str | None) -> str:
    normalized = _normalize_status(status)
    if normalized == "対応済み":
        return "完了"
    if normalized == "対応中":
        return "対応中"
    return "未着手"


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


def _sync_facility_non_cleaning_task(facility: dict[str, Any]) -> dict[str, Any] | None:
    facility_id = str(facility.get("id") or "").strip()
    if not facility_id:
        return None

    start_date = str(
        facility.get("start_date")
        or facility.get("report_date")
        or _today()
    )[:10]
    end_date = str(facility.get("end_date") or "")[:10] or None
    property_name = str(facility.get("property_name") or "").strip()
    room_name = str(facility.get("room_name") or "").strip()
    content = str(facility.get("content") or "").strip()
    note = str(facility.get("note") or "").strip()
    marker = f"設備対応ID：{facility_id}"

    place = " ".join([x for x in [property_name, room_name] if x]).strip()
    title = f"設備対応｜{place}" if place else "設備対応"

    note_parts = []
    if content:
        note_parts.append(content)
    if note:
        note_parts.append(note)
    note_parts.append(marker)

    payload = {
        "task_date": start_date,
        "status": _facility_task_status(facility.get("status")),
        "category": FACILITY_TASK_CATEGORY,
        "title": title,
        "deadline": end_date or start_date,
        "note": "\n".join(note_parts),
    }

    try:
        existing_res = (
            supabase.table("non_cleaning_tasks")
            .select("*")
            .ilike("note", f"%{marker}%")
            .limit(1)
            .execute()
        )
        existing = existing_res.data[0] if existing_res.data else None

        if existing:
            result = (
                supabase.table("non_cleaning_tasks")
                .update(payload)
                .eq("id", existing.get("id"))
                .execute()
            )
            row = result.data[0] if result.data else existing
            logger.info(
                f"facility non-cleaning task updated: facility_id={facility_id} task_id={row.get('id')} task_date={start_date}"
            )
            return row

        create_payload = {
            **payload,
            "assignee_ids": [],
            "assignee_names": [],
            "assignee_id": None,
            "assignee_name": None,
            "checker_id": None,
            "checker_name": None,
        }
        result = supabase.table("non_cleaning_tasks").insert(create_payload).execute()
        row = result.data[0] if result.data else None
        if row:
            logger.info(
                f"facility non-cleaning task created: facility_id={facility_id} task_id={row.get('id')} task_date={start_date}"
            )
        return row
    except Exception as e:
        logger.error(
            f"facility non-cleaning task sync failed: facility_id={facility_id} {e}",
            exc_info=True,
        )
        return None


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

    rows = res.data or []
    for row in rows:
        row["status"] = _normalize_status(row.get("status"))
    return rows


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
    current_user: dict = Depends(require_operational_write),
):
    normalized_status = _normalize_status(status)
    payload = {
        "property_id": property_id,
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
        logger.error(f"create_facility failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"設備情報の保存に失敗しました: {str(e)}")
    if not res.data:
        raise HTTPException(status_code=500, detail="設備情報の保存に失敗しました。")

    row = res.data[0]
    task = _sync_facility_non_cleaning_task(row)
    row["non_cleaning_task_id"] = str(task.get("id") or "") if task else ""
    return row


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
    current_user: dict = Depends(require_operational_write),
):
    normalized_status = _normalize_status(status) if status is not None else None
    payload: dict[str, Any] = {}
    for key, value in {
        "property_id": property_id,
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

    if not payload:
        raise HTTPException(status_code=400, detail="更新項目がありません。")

    try:
        res = supabase.table("facilities").update(payload).eq("id", facility_id).execute()
    except Exception as e:
        logger.error(f"update_facility failed: id={facility_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"設備情報の更新に失敗しました: {str(e)}")
    if not res.data:
        raise HTTPException(status_code=500, detail="設備情報の更新に失敗しました。")

    row = res.data[0]
    task = _sync_facility_non_cleaning_task(row)
    row["non_cleaning_task_id"] = str(task.get("id") or "") if task else ""
    return row


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

    row = res.data[0]
    task = _sync_facility_non_cleaning_task(row)
    return {
        "message": "設備トラブルを報告しました。",
        "data": row,
        "non_cleaning_task_id": str(task.get("id") or "") if task else "",
    }
