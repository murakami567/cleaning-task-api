from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, HTTPException

from app.db import supabase
from app.logger import get_logger
from app.services.auth_service import get_current_user

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


def _auto_progress_started_tasks():
    """
    「清掃開始」状態で cleaning_started_at から 1 分経過した清掃タスクを「清掃中」へ自動遷移させる。
    清掃外タスクは対象外。
    """
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        supabase.table("cleaning_tasks").update({"status": "清掃中"}).eq(
            "status", "清掃開始"
        ).lt("cleaning_started_at", cutoff).execute()
    except Exception as e:
        logger.error(f"auto_progress_started_tasks failed: {e}", exc_info=True)


# =========================================================
# 清掃タスク
# =========================================================
@router.get("/tasks/today")
def get_today_tasks():
    _auto_progress_started_tasks()

    today = _today_jst_iso()
    try:
        res = (
            supabase.table("cleaning_tasks")
            .select("*")
            .eq("task_date", today)
            .execute()
        )
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
        res = (
            supabase.table("cleaning_tasks")
            .select("*")
            .gt("task_date", today)
            .order("task_date")
            .execute()
        )
    except Exception as e:
        logger.error(f"get_future_tasks failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="将来のタスク取得に失敗しました。")

    logger.info(f"get_future_tasks: jst_date={today} count={len(res.data or [])}")
    return res.data


@router.post("/tasks/create")
def create_task(
    property_name: str = Body(...),
    room_name: str = Body(...),
    room_key: str = Body(...),
    task_date: str = Body(...),
    status: str = Body("未着手"),
    note: str = Body(""),
):
    payload = {
        "property_name": property_name,
        "room_name": room_name,
        "room_key": room_key,
        "task_date": task_date,
        "checkout_date": task_date,
        "next_checkin_date": None,
        "gap_nights": 0,
        "guest_count": 0,
        "load_score": 0,
        "status": status,
        "note": note,
        "source": "manual",
    }

    try:
        res = supabase.table("cleaning_tasks").insert(payload).execute()
    except Exception as e:
        logger.error(f"create_task failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="task creation failed")

    if not res.data:
        raise HTTPException(status_code=500, detail="task creation failed")

    logger.info(f"create_task: id={res.data[0].get('id')}")
    return res.data[0]


@router.post("/tasks/update")
def update_task(
    task_id: str = Body(...),
    task_date: str | None = Body(None),
    status: str | None = Body(None),
    note: str | None = Body(None),
    assigned_staff_ids: list[str] | None = Body(None),
    assigned_staff_names: list[str] | None = Body(None),
    assigned_staff_id: str | None = Body(None),
    assigned_staff_name: str | None = Body(None),
    checker_id: str | None = Body(None),
    checker_name: str | None = Body(None),
    assignment_locked: bool | None = Body(None),
):
    payload = {}

    if task_date is not None:
        payload["task_date"] = task_date

    if status is not None:
        payload["status"] = status
        if status == "清掃開始":
            payload["cleaning_started_at"] = datetime.now(timezone.utc).isoformat()
        else:
            payload["cleaning_started_at"] = None

    if note is not None:
        payload["note"] = note

    if assigned_staff_ids is not None:
        payload["assigned_staff_ids"] = assigned_staff_ids
        payload["assigned_staff_id"] = assigned_staff_ids[0] if len(assigned_staff_ids) > 0 else None

    if assigned_staff_names is not None:
        payload["assigned_staff_names"] = assigned_staff_names
        payload["assigned_staff_name"] = assigned_staff_names[0] if len(assigned_staff_names) > 0 else None

    if assigned_staff_id is not None:
        payload["assigned_staff_id"] = assigned_staff_id

    if assigned_staff_name is not None:
        payload["assigned_staff_name"] = assigned_staff_name

    if checker_id is not None:
        payload["checker_id"] = checker_id

    if checker_name is not None:
        payload["checker_name"] = checker_name

    if assignment_locked is not None:
        payload["assignment_locked"] = assignment_locked

    if not payload:
        raise HTTPException(status_code=400, detail="no update fields")

    try:
        res = (
            supabase.table("cleaning_tasks")
            .update(payload)
            .eq("id", task_id)
            .execute()
        )
    except Exception as e:
        logger.error(f"update_task failed: task_id={task_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"supabase update failed: {str(e)}")

    logger.info(f"update_task: task_id={task_id}")
    return {"ok": True, "task_id": task_id, "updated": payload, "data": res.data}


# =========================================================
# 指定日タスク（過去も未来も対応）
# =========================================================
@router.get("/tasks/by-date")
def get_tasks_by_date(date: str):
    if not date:
        raise HTTPException(status_code=400, detail="date is required")

    _auto_progress_started_tasks()

    try:
        res = (
            supabase.table("cleaning_tasks")
            .select("*")
            .eq("task_date", date)
            .order("task_date")
            .execute()
        )
    except Exception as e:
        logger.error(f"get_tasks_by_date failed: date={date} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="タスク取得に失敗しました。")

    logger.info(f"get_tasks_by_date: date={date} count={len(res.data or [])}")
    return res.data


# =========================================================
# 清掃外タスク
# =========================================================
@router.get("/non-cleaning-tasks")
def get_non_cleaning_tasks():
    try:
        res = (
            supabase.table("non_cleaning_tasks")
            .select("*")
            .order("task_date")
            .execute()
        )
    except Exception as e:
        logger.error(f"get_non_cleaning_tasks failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="非清掃タスク取得に失敗しました。")

    rows = [_normalize_non_cleaning_row(row) for row in (res.data or [])]
    logger.info(f"get_non_cleaning_tasks: count={len(rows)}")
    return rows


@router.post("/non-cleaning-tasks/create")
def create_non_cleaning_task(
    task_date: str = Body(...),
    status: str = Body("未着手"),
    category: str = Body("OTHER"),
    title: str = Body(...),
    deadline: str | None = Body(None),
    assignee_ids: list[str] | None = Body(None),
    assignee_names: list[str] | None = Body(None),
    checker_id: str | None = Body(None),
    checker_name: str | None = Body(None),
    note: str = Body(""),
):
    payload = {
        "task_date": task_date,
        "status": _normalize_non_cleaning_status(status),
        "category": category,
        "title": title,
        "deadline": deadline,
        "assignee_ids": assignee_ids or [],
        "assignee_names": assignee_names or [],
        "checker_id": checker_id,
        "checker_name": checker_name,
        "note": note,
    }

    payload["assignee_id"] = assignee_ids[0] if assignee_ids and len(assignee_ids) > 0 else None
    payload["assignee_name"] = assignee_names[0] if assignee_names and len(assignee_names) > 0 else None

    try:
        res = supabase.table("non_cleaning_tasks").insert(payload).execute()
    except Exception as e:
        logger.error(f"create_non_cleaning_task failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="非清掃タスク作成に失敗しました。")

    if not res.data:
        raise HTTPException(status_code=500, detail="非清掃タスク作成に失敗しました。")

    logger.info(f"create_non_cleaning_task: id={res.data[0].get('id')}")
    return _normalize_non_cleaning_row(res.data[0])


@router.post("/non-cleaning-tasks/update")
def update_non_cleaning_task(
    task_id: str = Body(...),
    task_date: str | None = Body(None),
    status: str | None = Body(None),
    category: str | None = Body(None),
    title: str | None = Body(None),
    deadline: str | None = Body(None),
    assignee_ids: list[str] | None = Body(None),
    assignee_names: list[str] | None = Body(None),
    checker_id: str | None = Body(None),
    checker_name: str | None = Body(None),
    note: str | None = Body(None),
):
    payload = {}
    if task_date is not None:
        payload["task_date"] = task_date
    if status is not None:
        payload["status"] = _normalize_non_cleaning_status(status)
    if category is not None:
        payload["category"] = category
    if title is not None:
        payload["title"] = title
    if deadline is not None:
        payload["deadline"] = deadline
    if assignee_ids is not None:
        payload["assignee_ids"] = assignee_ids
        payload["assignee_id"] = assignee_ids[0] if len(assignee_ids) > 0 else None
    if assignee_names is not None:
        payload["assignee_names"] = assignee_names
        payload["assignee_name"] = assignee_names[0] if len(assignee_names) > 0 else None
    if checker_id is not None:
        payload["checker_id"] = checker_id
    if checker_name is not None:
        payload["checker_name"] = checker_name
    if note is not None:
        payload["note"] = note

    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")
    if not payload:
        raise HTTPException(status_code=400, detail="no update fields")

    try:
        res = supabase.table("non_cleaning_tasks").update(payload).eq("id", task_id).execute()
    except Exception as e:
        logger.error(f"update_non_cleaning_task failed: task_id={task_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="非清掃タスク更新に失敗しました。")

    return {"ok": True, "task_id": task_id, "updated": payload, "data": [_normalize_non_cleaning_row(row) for row in (res.data or [])]}


@router.post("/non-cleaning-tasks/delete")
def delete_non_cleaning_task(task_id: str = Body(...)):
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")
    try:
        res = supabase.table("non_cleaning_tasks").delete().eq("id", task_id).execute()
    except Exception as e:
        logger.error(f"delete_non_cleaning_task failed: task_id={task_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="非清掃タスク削除に失敗しました。")
    return {"ok": True, "task_id": task_id, "data": res.data or []}
