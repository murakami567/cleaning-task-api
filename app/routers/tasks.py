from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, HTTPException

from app.db import supabase
from app.logger import get_logger
from app.services.auth_service import get_current_user

router = APIRouter(tags=["tasks"])
logger = get_logger(__name__)


def _auto_progress_started_tasks():
    """
    「清掃開始」状態で cleaning_started_at から 1 分経過したタスクを「清掃中」へ自動遷移させる。
    一覧取得系エンドポイントの先頭で呼ぶ。失敗しても取得処理自体は継続する。
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
    from datetime import date

    _auto_progress_started_tasks()

    today = date.today().isoformat()
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

    logger.info(f"get_today_tasks: date={today} count={len(res.data or [])}")
    return res.data


@router.get("/tasks/future")
def get_future_tasks():
    from datetime import date

    _auto_progress_started_tasks()

    today = date.today().isoformat()
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

    logger.info(f"get_future_tasks: count={len(res.data or [])}")
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

    # 清掃日の更新
    # checkout_date / next_checkin_date は変更しない
    if task_date is not None:
        payload["task_date"] = task_date

    if status is not None:
        payload["status"] = status
        # 「清掃開始」になった瞬間にサーバ側で開始時刻を記録する。
        # それ以外のステータスに遷移したら開始時刻はクリアして残骸を残さない。
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
    return {
        "ok": True,
        "task_id": task_id,
        "updated": payload,
        "data": res.data,
    }

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

    logger.info(f"get_non_cleaning_tasks: count={len(res.data or [])}")
    return res.data


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
        "status": status,
        "category": category,
        "title": title,
        "deadline": deadline,
        "assignee_ids": assignee_ids or [],
        "assignee_names": assignee_names or [],
        "checker_id": checker_id,
        "checker_name": checker_name,
        "note": note,
    }

    # 旧単数列も残すなら先頭だけ入れる
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
    return res.data[0]
