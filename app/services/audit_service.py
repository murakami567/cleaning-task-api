from typing import Any

from app.db import supabase
from app.logger import get_logger

logger = get_logger(__name__)

SENSITIVE_KEYS = {"password", "token", "access_token", "authorization", "secret", "jwt"}


def sanitize_audit_data(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ("[REDACTED]" if key.lower() in SENSITIVE_KEYS else sanitize_audit_data(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_audit_data(item) for item in value]
    return value


def write_audit_log(
    *,
    actor_id: str | None,
    actor_role: str | None,
    source: str,
    action: str,
    page: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    target_name: str | None = None,
    before_data: dict | None = None,
    after_data: dict | None = None,
    result: str = "success",
    error_message: str | None = None,
    metadata: dict | None = None,
) -> None:
    actor_name = None
    if actor_id:
        try:
            actor_res = supabase.table("staff_members").select("staff_name,role").eq("id", actor_id).limit(1).execute()
            if actor_res.data:
                actor_name = actor_res.data[0].get("staff_name")
                actor_role = actor_res.data[0].get("role") or actor_role
        except Exception as exc:
            logger.warning(f"audit actor lookup failed actor_id={actor_id}: {exc}")

    row = {
        "actor_id": actor_id,
        "actor_name": actor_name,
        "actor_role": actor_role,
        "source": source,
        "action": action,
        "page": page,
        "target_type": target_type,
        "target_id": target_id,
        "target_name": target_name,
        "before_data": sanitize_audit_data(before_data),
        "after_data": sanitize_audit_data(after_data),
        "result": result,
        "error_message": error_message,
        "metadata": sanitize_audit_data(metadata or {}),
    }

    try:
        supabase.table("audit_logs").insert(row).execute()
    except Exception as exc:
        logger.error(f"audit log write failed action={action} actor_id={actor_id}: {exc}", exc_info=True)
