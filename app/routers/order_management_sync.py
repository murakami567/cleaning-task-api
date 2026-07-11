from fastapi import APIRouter, Body, HTTPException

from app.db import supabase
from app.logger import get_logger

router = APIRouter(tags=["order-management-sync"])
logger = get_logger(__name__)


def _normalize_row(row: dict) -> dict:
    value = dict(row or {})
    status = str(value.get("status") or "未着手").strip()
    if status not in {"未着手", "対応中", "完了"}:
        value["status"] = "未着手"
    return value


def _normalize_category(category: str | None) -> str:
    value = str(category or "").strip()
    if value in {"荷受け", "LINEN"}:
        return "LINEN"
    return value or "OTHER"


@router.post("/integrations/order-management/non-cleaning-task-sync")
def sync_order_management_task(
    source_order_id: str = Body(...),
    task_id: str | None = Body(None),
    task_date: str = Body(...),
    category: str = Body("LINEN"),
    title: str = Body(...),
    deadline: str | None = Body(None),
    note: str = Body(""),
):
    if not source_order_id.strip():
        raise HTTPException(status_code=400, detail="source_order_id is required")
    if not task_date:
        raise HTTPException(status_code=400, detail="task_date is required")
    if not title.strip():
        raise HTTPException(status_code=400, detail="title is required")

    payload = {
        "task_date": task_date,
        "category": _normalize_category(category),
        "title": title.strip(),
        "deadline": deadline or task_date,
        "note": note,
    }

    existing = None

    try:
        if task_id:
            result = (
                supabase.table("non_cleaning_tasks")
                .select("*")
                .eq("id", task_id)
                .limit(1)
                .execute()
            )
            if result.data:
                existing = result.data[0]

        if existing is None:
            marker = f"発注管理ID：{source_order_id}"
            result = (
                supabase.table("non_cleaning_tasks")
                .select("*")
                .ilike("note", f"%{marker}%")
                .limit(1)
                .execute()
            )
            if result.data:
                existing = result.data[0]

        if existing is not None:
            result = (
                supabase.table("non_cleaning_tasks")
                .update(payload)
                .eq("id", existing["id"])
                .execute()
            )
            if not result.data:
                raise HTTPException(status_code=500, detail="清掃外タスクの更新に失敗しました。")
            row = _normalize_row(result.data[0])
            logger.info(
                f"order management task updated: source_order_id={source_order_id} task_id={row.get('id')}"
            )
            return {"action": "updated", "task": row, "task_id": str(row.get("id") or "")}

        create_payload = {
            **payload,
            "status": "未着手",
            "assignee_ids": [],
            "assignee_names": [],
            "assignee_id": None,
            "assignee_name": None,
            "checker_id": None,
            "checker_name": None,
        }
        result = supabase.table("non_cleaning_tasks").insert(create_payload).execute()
        if not result.data:
            raise HTTPException(status_code=500, detail="清掃外タスクの作成に失敗しました。")

        row = _normalize_row(result.data[0])
        logger.info(
            f"order management task created: source_order_id={source_order_id} task_id={row.get('id')}"
        )
        return {"action": "created", "task": row, "task_id": str(row.get("id") or "")}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"sync_order_management_task failed: source_order_id={source_order_id} task_id={task_id} {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="発注管理からの清掃外タスク同期に失敗しました。")
