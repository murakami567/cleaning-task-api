import jwt
from fastapi import Request

from app.services.audit_service import write_audit_log
from app.services.auth_service import JWT_ALGORITHM, JWT_SECRET


# ログイン・アカウント編集は各ルーター側で詳細監査済みのため、ここでは重複記録しない。
# 日常運用系の重要な更新をサーバー側で一括監査する。
AUDIT_PATHS = {
    "/tasks/create": ("task_create", "task", "tasks"),
    "/tasks/update": ("task_update", "task", "tasks"),
    "/non-cleaning-tasks/create": ("non_cleaning_task_create", "non_cleaning_task", "tasks"),
    "/non-cleaning-tasks/update": ("non_cleaning_task_update", "non_cleaning_task", "tasks"),
    "/facilities/create": ("facility_create", "facility", "facilities"),
    "/facilities/update": ("facility_update", "facility", "facilities"),
}

AUDIT_PREFIXES = (
    ("/api/admin-portal/", "admin_operation", "admin_portal"),
    ("/api/employee/", "employee_operation", "employee"),
    ("/staff-schedules/", "schedule_update", "schedules"),
    ("/properties/", "property_update", "properties"),
    ("/rooms/", "room_update", "rooms"),
    ("/payroll/", "payroll_update", "payroll"),
)

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _actor_from_request(request: Request):
    authorization = request.headers.get("authorization", "")
    if not authorization.lower().startswith("bearer "):
        return None
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        return None
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = payload.get("user_id")
        if not user_id:
            return None
        return {"user_id": str(user_id), "role": payload.get("role")}
    except Exception:
        return None


def _event_for(request: Request):
    if request.method.upper() not in MUTATING_METHODS:
        return None

    path = request.url.path
    if path in {"/api/audit-logs", "/api/auth/login", "/staffs/upsert"}:
        return None

    exact = AUDIT_PATHS.get(path)
    if exact:
        return exact

    for prefix, action, page in AUDIT_PREFIXES:
        if path.startswith(prefix):
            return action, "operation", page
    return None


async def audit_write_middleware(request: Request, call_next):
    event = _event_for(request)
    actor = _actor_from_request(request) if event else None

    try:
        response = await call_next(request)
    except Exception as exc:
        if event and actor:
            action, target_type, page = event
            write_audit_log(
                actor_id=actor["user_id"],
                actor_role=actor.get("role"),
                source="employee" if request.url.path.startswith("/api/employee/") else "admin",
                action=action,
                page=page,
                target_type=target_type,
                result="failure",
                error_message=str(exc),
                metadata={"method": request.method, "path": request.url.path},
            )
        raise

    if event and actor:
        action, target_type, page = event
        success = response.status_code < 400
        write_audit_log(
            actor_id=actor["user_id"],
            actor_role=actor.get("role"),
            source="employee" if request.url.path.startswith("/api/employee/") else "admin",
            action=action,
            page=page,
            target_type=target_type,
            result="success" if success else "failure",
            error_message=None if success else f"HTTP {response.status_code}",
            metadata={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
            },
        )

    return response
