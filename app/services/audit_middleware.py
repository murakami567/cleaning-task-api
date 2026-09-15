import json

import jwt
from fastapi import Request

from app.db import supabase
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
TASK_AUDIT_FIELDS = {
    "task_date", "status", "note", "assigned_staff_ids", "assigned_staff_names",
    "assigned_staff_id", "assigned_staff_name", "checker_id", "checker_name",
    "assignment_locked", "early_checkin_time", "late_checkout_time",
}


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


def _fetch_task(task_id: str | None):
    if not task_id:
        return None
    try:
        res = supabase.table("cleaning_tasks").select("*").eq("id", task_id).limit(1).execute()
        return dict(res.data[0]) if res.data else None
    except Exception:
        return None


def _task_snapshot(row: dict | None):
    if not row:
        return None
    return {key: row.get(key) for key in TASK_AUDIT_FIELDS}


def _task_target_name(row: dict | None):
    if not row:
        return None
    prop = str(row.get("property_name") or "").strip()
    room = str(row.get("room_name") or "").strip()
    if prop and room:
        return f"{prop} / {room}"
    return prop or room or None


async def audit_write_middleware(request: Request, call_next):
    event = _event_for(request)
    actor = _actor_from_request(request) if event else None

    task_id = None
    task_before = None
    if event and actor and request.url.path == "/tasks/update":
        try:
            raw_body = await request.body()
            body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
            if isinstance(body, dict):
                task_id = str(body.get("task_id") or "").strip() or None
                task_before = _fetch_task(task_id)
        except Exception:
            task_id = None
            task_before = None

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
                target_id=task_id if action == "task_update" else None,
                target_name=_task_target_name(task_before) if action == "task_update" else None,
                before_data=_task_snapshot(task_before) if action == "task_update" else None,
                result="failure",
                error_message=str(exc),
                metadata={"method": request.method, "path": request.url.path},
            )
        raise

    if event and actor:
        action, target_type, page = event
        success = response.status_code < 400

        task_after = _fetch_task(task_id) if action == "task_update" and success else None
        before_snapshot = _task_snapshot(task_before) if action == "task_update" else None
        after_snapshot = _task_snapshot(task_after) if action == "task_update" else None

        # UIから同じ値の更新APIが連続して呼ばれる場合は監査ログを増やさない。
        # 実際にDB上の監査対象項目が変化した更新だけを残す。
        if action == "task_update" and success and before_snapshot == after_snapshot:
            return response

        write_audit_log(
            actor_id=actor["user_id"],
            actor_role=actor.get("role"),
            source="employee" if request.url.path.startswith("/api/employee/") else "admin",
            action=action,
            page=page,
            target_type=target_type,
            target_id=task_id if action == "task_update" else None,
            target_name=_task_target_name(task_after or task_before) if action == "task_update" else None,
            before_data=before_snapshot,
            after_data=after_snapshot,
            result="success" if success else "failure",
            error_message=None if success else f"HTTP {response.status_code}",
            metadata={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
            },
        )

    return response
