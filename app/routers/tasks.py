from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.db import supabase
from app.logger import get_logger
from app.services.auth_service import require_operational_write, require_task_write

router = APIRouter(tags=["tasks"])
logger = get_logger(__name__)

NON_CLEANING_STATUS_VALUES = {"未着手", "対応中", "完了"}
NON_CLEANING_STATUS_MAP = {
    "清掃開始": "対応中",
    "清掃中": "対応中",
    "作業中": "対応中",
    "対応済み": "完了",
    "清掃完了": "完了",
}


def _today_jst_iso() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=9)).date().isoformat()


def _normalize_non_cleaning_status(status: str | None) -> str:
    value = str(status or "未着手").strip()
    value = NON_CLEANING_STATUS_MAP.get(value, value)
    if value not in NON_CLEANING_STATUS_VALUES:
        raise HTTPException(status_code=400, detail="清掃外タスクのステータスは 未着手・対応中・完了 のみ指定できます。")
    return value


def _normalize_non_cleaning_row(row: dict):
    row = dict(row or {})
    status = str(row.get("status") or "未着手").strip()
    row["status"] = NON_CLEANING_STATUS_MAP.get(status, status)
    if row["status"] not in NON_CLEANING_STATUS_VALUES:
        row["status"] = "未着手"
    return row


def _extract_task_id(body: Any = None, task_id: str | None = None, id: str | None = None) -> str:
    if task_id:
        return str(task_id).strip()
    if id:
        return str(id).strip()
    if isinstance(body, dict):
        return str(body.get("task_id") or body.get("id") or body.get("non_cleaning_task_id") or "").strip()
    if isinstance(body, str):
        return body.strip()
    return ""


def _resolve_staff_names(staff_ids: list[str]) -> list[str]:
    """担当者IDは必ず staff_members を正として氏名へ変換する。UUIDを氏名欄へ保存しない。"""
    clean_ids = [str(v).strip() for v in (staff_ids or []) if str(v or "").strip()]
    if not clean_ids:
        return []
    try:
        res = (
            supabase.table("staff_members")
            .select("id,staff_name")
            .in_("id", clean_ids)
            .execute()
        )
    except Exception as e:
        logger.error(f"resolve_staff_names failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="担当者名の解決に失敗しました。")

    by_id = {
        str(row.get("id")): str(row.get("staff_name") or "").strip()
        for row in (res.data or [])
        if row.get("id") and str(row.get("staff_name") or "").strip()
    }
    missing = [sid for sid in clean_ids if sid not in by_id]
    if missing:
        logger.warning(f"resolve_staff_names: unknown staff ids={missing}")
        raise HTTPException(status_code=422, detail="存在しない担当者が含まれています。")
    return [by_id[sid] for sid in clean_ids]


def _auto_progress_started_tasks():
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        supabase.table("cleaning_tasks").update({"status": "清掃中"}).eq(
            "status", "清掃開始"
        ).lt("cleaning_started_at", cutoff).execute()
    except Exception as e:
        logger.error(f"auto_progress_started_tasks failed: {e}", exc_info=True)


@router.get("/tasks/today")
def get_today_tasks():
    _auto_progress_started_tasks()
    today = _today_jst_iso()
    try:
        res = supabase.table("cleaning_tasks").select("*").eq("task_date", today).execute()
    except Exception as e:
        logger.error(f"get_today_tasks failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="本日のタスク取得に失敗しました。")
    logger.info(f"get_today_tasks: jst_date={today} count={len(res.data or [])}")
    return res.data


@router.get("/tasks/future")
def get_future_tasks():
    _auto_progress_started_tasks()
    today = _today_jst_iso()
    try:
        res = supabase.table("cleaning_tasks").select("*").gt("task_date", today).order("task_date").execute()
    except Exception as e:
        logger.error(f"get_future_tasks failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="将来のタスク取得に失敗しました。")
    logger.info(f"get_future_tasks: jst_date={today} count={len(res.data or [])}")
    return res.data


@router.post("/tasks/create")
def create_task(
    property_name: str = Body(...), room_name: str = Body(...), room_key: str = Body(...),
    task_date: str = Body(...), status: str = Body("未着手"), note: str = Body(""),
    current_user: dict = Depends(require_operational_write),
):
    payload = {"property_name": property_name, "room_name": room_name, "room_key": room_key,
        "task_date": task_date, "checkout_date": task_date, "next_checkin_date": None,
        "gap_nights": 0, "guest_count": 0, "load_score": 0, "status": status,
        "note": note, "source": "manual"}
    try:
        res = supabase.table("cleaning_tasks").insert(payload).execute()
    except Exception as e:
        logger.error(f"create_task failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="task creation failed")
    if not res.data:
        raise HTTPException(status_code=500, detail="task creation failed")
    return res.data[0]


@router.post("/tasks/update")
def update_task(
    task_id: str = Body(...), task_date: str | None = Body(None), status: str | None = Body(None),
    note: str | None = Body(None), assigned_staff_ids: list[str] | None = Body(None),
    assigned_staff_names: list[str] | None = Body(None), assigned_staff_id: str | None = Body(None),
    assigned_staff_name: str | None = Body(None), checker_id: str | None = Body(None),
    checker_name: str | None = Body(None), assignment_locked: bool | None = Body(None),
    early_checkin_time: str | None = Body(None), late_checkout_time: str | None = Body(None),
    current_user: dict = Depends(require_task_write),
):
    payload = {}
    if task_date is not None:
        payload["task_date"] = task_date
    if status is not None:
        payload["status"] = status
        payload["cleaning_started_at"] = datetime.now(timezone.utc).isoformat() if status == "清掃開始" else None
    if note is not None:
        payload["note"] = note

    # 担当者はIDを正として staff_members から氏名を再解決する。
    # クライアントから渡された assigned_staff_names / assigned_staff_name は信用しない。
    if assigned_staff_ids is not None:
        clean_ids = [str(v).strip() for v in assigned_staff_ids if str(v or "").strip()]
        resolved_names = _resolve_staff_names(clean_ids)
        payload["assigned_staff_ids"] = clean_ids
        payload["assigned_staff_names"] = resolved_names
        payload["assigned_staff_id"] = clean_ids[0] if clean_ids else None
        payload["assigned_staff_name"] = resolved_names[0] if resolved_names else None
    elif assigned_staff_id is not None:
        sid = str(assigned_staff_id or "").strip()
        resolved_names = _resolve_staff_names([sid]) if sid else []
        payload["assigned_staff_id"] = sid or None
        payload["assigned_staff_name"] = resolved_names[0] if resolved_names else None

    if checker_id is not None:
        payload["checker_id"] = checker_id
    if checker_name is not None:
        payload["checker_name"] = checker_name
    if assignment_locked is not None:
        payload["assignment_locked"] = assignment_locked
    if early_checkin_time is not None:
        payload["early_checkin_time"] = early_checkin_time or None
    if late_checkout_time is not None:
        payload["late_checkout_time"] = late_checkout_time or None
    if not payload:
        raise HTTPException(status_code=400, detail="no update fields")
    try:
        res = supabase.table("cleaning_tasks").update(payload).eq("id", task_id).execute()
    except Exception as e:
        logger.error(f"update_task failed: task_id={task_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"supabase update failed: {str(e)}")
    logger.info(f"update_task: task_id={task_id}")
    return {"ok": True, "task_id": task_id, "updated": payload, "data": res.data}


@router.get("/tasks/by-date")
def get_tasks_by_date(date: str):
    if not date:
        raise HTTPException(status_code=400, detail="date is required")
    _auto_progress_started_tasks()
    try:
        res = supabase.table("cleaning_tasks").select("*").eq("task_date", date).execute()
    except Exception as e:
        logger.error(f"get_tasks_by_date failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="指定日のタスク取得に失敗しました。")
    return res.data
