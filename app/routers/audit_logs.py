from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.db import supabase
from app.logger import get_logger
from app.services.audit_service import sanitize_audit_data
from app.services.auth_service import get_current_user, require_master_admin

router = APIRouter(tags=["audit_logs"])
logger = get_logger(__name__)


class AuditLogCreate(BaseModel):
    source: Literal["admin", "mobile", "employee", "master", "system"]
    action: str = Field(min_length=1, max_length=100)
    page: str | None = Field(default=None, max_length=200)
    target_type: str | None = Field(default=None, max_length=100)
    target_id: str | None = Field(default=None, max_length=200)
    target_name: str | None = Field(default=None, max_length=300)
    before_data: dict[str, Any] | None = None
    after_data: dict[str, Any] | None = None
    result: Literal["success", "failure"] = "success"
    error_message: str | None = Field(default=None, max_length=1000)
    metadata: dict[str, Any] = Field(default_factory=dict)


def _actor_snapshot(user_id: str) -> tuple[str | None, str | None]:
    try:
        res = supabase.table("staff_members").select("staff_name,role").eq("id", user_id).limit(1).execute()
        if res.data:
            row = res.data[0]
            return row.get("staff_name"), row.get("role")
    except Exception as exc:
        logger.warning(f"audit actor lookup failed user_id={user_id}: {exc}")
    return None, None


@router.post("/api/audit-logs", status_code=201)
def create_audit_log(payload: AuditLogCreate, current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    actor_name, db_role = _actor_snapshot(user_id)
    actor_role = db_role or current_user.get("role")

    row = {
        "actor_id": user_id,
        "actor_name": actor_name,
        "actor_role": actor_role,
        "source": payload.source,
        "action": payload.action,
        "page": payload.page,
        "target_type": payload.target_type,
        "target_id": payload.target_id,
        "target_name": payload.target_name,
        "before_data": sanitize_audit_data(payload.before_data),
        "after_data": sanitize_audit_data(payload.after_data),
        "result": payload.result,
        "error_message": payload.error_message,
        "metadata": sanitize_audit_data(payload.metadata),
    }

    try:
        res = supabase.table("audit_logs").insert(row).execute()
    except Exception as exc:
        logger.error(f"audit log insert failed user_id={user_id}: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="監査ログの保存に失敗しました。")

    if not res.data:
        raise HTTPException(status_code=500, detail="監査ログの保存に失敗しました。")
    return {"ok": True, "id": res.data[0].get("id")}


@router.get("/api/master/audit-logs")
def list_audit_logs(
    limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0),
    source: str | None = Query(default=None), actor_id: str | None = Query(default=None),
    action: str | None = Query(default=None), current_user: dict = Depends(require_master_admin),
):
    try:
        query = supabase.table("audit_logs").select("*").order("created_at", desc=True)
        if source: query = query.eq("source", source)
        if actor_id: query = query.eq("actor_id", actor_id)
        if action: query = query.eq("action", action)
        res = query.range(offset, offset + limit - 1).execute()
    except Exception as exc:
        logger.error(f"audit log read failed master_user_id={current_user.get('user_id')}: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="監査ログの取得に失敗しました。")
    return {"items": res.data or [], "limit": limit, "offset": offset}
