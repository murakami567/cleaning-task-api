from datetime import datetime, timedelta, timezone
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
    except Exception as exc: logger.warning(f"audit actor lookup failed user_id={user_id}: {exc}")
    return None, None

@router.post("/api/audit-logs", status_code=201)
def create_audit_log(payload: AuditLogCreate, current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    actor_name, db_role = _actor_snapshot(user_id)
    row = {"actor_id": user_id, "actor_name": actor_name, "actor_role": db_role or current_user.get("role"), "source": payload.source, "action": payload.action, "page": payload.page, "target_type": payload.target_type, "target_id": payload.target_id, "target_name": payload.target_name, "before_data": sanitize_audit_data(payload.before_data), "after_data": sanitize_audit_data(payload.after_data), "result": payload.result, "error_message": payload.error_message, "metadata": sanitize_audit_data(payload.metadata)}
    try: res = supabase.table("audit_logs").insert(row).execute()
    except Exception as exc:
        logger.error(f"audit log insert failed user_id={user_id}: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="監査ログの保存に失敗しました。")
    if not res.data: raise HTTPException(status_code=500, detail="監査ログの保存に失敗しました。")
    return {"ok": True, "id": res.data[0].get("id")}

@router.get("/api/master/audit-logs")
def list_audit_logs(limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0), source: str | None = Query(default=None), actor_id: str | None = Query(default=None), action: str | None = Query(default=None), current_user: dict = Depends(require_master_admin)):
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

@router.get("/api/master/account-audit")
def account_permission_audit(current_user: dict = Depends(require_master_admin)):
    try:
        staff_res = supabase.table("staff_members").select("id,staff_code,staff_name,role,is_active,created_at,updated_at").order("staff_code").execute()
        history_res = supabase.table("audit_logs").select("id,actor_name,actor_role,action,target_id,target_name,before_data,after_data,result,created_at").in_("action", ["staff_create", "staff_update"]).order("created_at", desc=True).limit(100).execute()
    except Exception as exc:
        logger.error(f"account audit read failed master_user_id={current_user.get('user_id')}: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="アカウント・権限監査情報の取得に失敗しました。")
    staff = staff_res.data or []
    privileged_roles = {"master_admin", "admin", "sub_admin", "payroll_admin"}
    summary = {"total": len(staff), "active": sum(1 for row in staff if row.get("is_active") is True), "inactive": sum(1 for row in staff if row.get("is_active") is not True), "privileged": sum(1 for row in staff if row.get("role") in privileged_roles), "master_admin": sum(1 for row in staff if row.get("role") == "master_admin")}
    return {"summary": summary, "accounts": staff, "history": history_res.data or []}

@router.get("/api/master/system-monitor")
def system_monitor(current_user: dict = Depends(require_master_admin)):
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=24)
    try:
        res = supabase.table("audit_logs").select("id,actor_name,actor_role,source,action,page,target_type,target_name,result,error_message,metadata,created_at").gte("created_at", since.isoformat()).order("created_at", desc=True).limit(5000).execute()
    except Exception as exc:
        logger.error(f"system monitor read failed master_user_id={current_user.get('user_id')}: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="システム監視情報の取得に失敗しました。")
    rows = res.data or []
    failures = [row for row in rows if row.get("result") == "failure"]
    auth_failures = [row for row in failures if row.get("action") in {"login", "auth_failure", "login_failure"} or row.get("page") == "login"]
    source_counts = {key: 0 for key in ["admin", "mobile", "employee", "master", "system"]}
    for row in failures:
        source = row.get("source") or "system"
        source_counts[source] = source_counts.get(source, 0) + 1

    current_hour = now.replace(minute=0, second=0, microsecond=0)
    first_hour = current_hour - timedelta(hours=23)
    buckets = {}
    for i in range(24):
        hour = first_hour + timedelta(hours=i)
        key = hour.isoformat()
        buckets[key] = {"hour": key, "operations": 0, "failures": 0}
    for row in rows:
        try:
            created = datetime.fromisoformat(str(row.get("created_at", "")).replace("Z", "+00:00")).astimezone(timezone.utc)
            hour = created.replace(minute=0, second=0, microsecond=0)
            key = hour.isoformat()
            if key in buckets:
                buckets[key]["operations"] += 1
                if row.get("result") == "failure": buckets[key]["failures"] += 1
        except (TypeError, ValueError):
            continue

    return {"status": "normal" if not failures else ("warning" if len(failures) < 10 else "critical"), "period_hours": 24, "summary": {"operations": len(rows), "failures": len(failures), "auth_failures": len(auth_failures), "latest_failure": failures[0].get("created_at") if failures else None}, "source_counts": source_counts, "hourly_trend": list(buckets.values()), "failures": failures[:100]}
