from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from app.db import supabase
from app.logger import get_logger

router = APIRouter(tags=["tasks"])
logger = get_logger(__name__)


@router.post("/tasks/update")
def update_task_with_times(
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
    checklist: dict[str, Any] | None = Body(None),
    checked_by_name: str | None = Body(None),
):
    """
    清掃タスク更新。

    時刻ルール:
    - 清掃開始: cleaning_started_at を記録
    - 清掃中: cleaning_started_at は消さない
    - 清掃完了/完了: cleaning_completed_at を記録
    - チェック完了: checked_at を記録
    - checklist が送られてきた場合は cleaning_tasks.checklist に保存
    """
    payload = {}
    now = datetime.now(timezone.utc).isoformat()

    if task_date is not None:
        payload["task_date"] = task_date

    if status is not None:
        payload["status"] = status

        if status == "清掃開始":
            payload["cleaning_started_at"] = now
            payload["cleaning_completed_at"] = None
            payload["checked_at"] = None
        elif status == "清掃中":
            # 開始時刻は保持する
            pass
        elif status in ["清掃完了", "完了"]:
            payload["cleaning_completed_at"] = now
        elif status == "チェック完了":
            payload["checked_at"] = now
            if checked_by_name is not None:
                payload["checked_by_name"] = checked_by_name
        elif status in ["未着手", "キャンセル", "清掃不要", "CXL"]:
            payload["cleaning_started_at"] = None
            payload["cleaning_completed_at"] = None
            payload["checked_at"] = None
            payload["checked_by_name"] = None

    if checklist is not None:
        payload["checklist"] = checklist

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
        logger.error(f"update_task_with_times failed: task_id={task_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"supabase update failed: {str(e)}")

    logger.info(f"update_task_with_times: task_id={task_id} status={status}")
    return {
        "ok": True,
        "task_id": task_id,
        "updated": payload,
        "data": res.data,
    }
