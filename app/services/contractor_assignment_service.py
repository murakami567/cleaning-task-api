from datetime import datetime, timedelta, timezone
from typing import Any

from app.db import supabase


CONTRACTOR_ASSIGN_START_DAYS = 3
CONTRACTOR_ASSIGN_END_DAYS = 7


def _today_jst():
    return (datetime.now(timezone.utc) + timedelta(hours=9)).date()


def contractor_assignment_range() -> tuple[str, str]:
    """通常の自動割当と同じ「今日+3日〜今日+7日」。"""
    today = _today_jst()
    return (
        (today + timedelta(days=CONTRACTOR_ASSIGN_START_DAYS)).isoformat(),
        (today + timedelta(days=CONTRACTOR_ASSIGN_END_DAYS)).isoformat(),
    )


def _within_assignment_range(task_date: str | None) -> bool:
    value = str(task_date or "")[:10]
    if not value:
        return False
    start_date, end_date = contractor_assignment_range()
    return start_date <= value <= end_date


def _property_name_map() -> dict[str, str]:
    res = supabase.table("properties").select("id,property_name,is_active").execute()
    return {
        str(row.get("id")): str(row.get("property_name") or "").strip()
        for row in (res.data or [])
        if row.get("id") and row.get("property_name") and row.get("is_active") is not False
    }


def _contractors() -> list[dict[str, Any]]:
    res = (
        supabase.table("staff_members")
        .select("id,staff_name,role,is_active,available_property_ids,unchecked_property_ids")
        .eq("role", "contractor")
        .eq("is_active", True)
        .execute()
    )
    return res.data or []


def contractor_by_property_name() -> dict[str, dict[str, Any]]:
    names = _property_name_map()
    out: dict[str, dict[str, Any]] = {}
    for staff in _contractors():
        property_ids = list(staff.get("available_property_ids") or []) + list(staff.get("unchecked_property_ids") or [])
        for property_id in dict.fromkeys(str(x) for x in property_ids if x):
            property_name = names.get(property_id)
            if property_name:
                out[property_name] = staff
    return out


def apply_contractor_to_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """新規清掃タスク用。通常割当期間内の対象物件だけ委託業者をセットする。"""
    if not _within_assignment_range(payload.get("task_date")):
        return payload

    property_name = str(payload.get("property_name") or "").strip()
    staff = contractor_by_property_name().get(property_name)
    if not staff:
        return payload
    sid = str(staff.get("id") or "")
    name = str(staff.get("staff_name") or "")
    if not sid:
        return payload
    payload["assigned_staff_ids"] = [sid]
    payload["assigned_staff_names"] = [name]
    payload["assigned_staff_id"] = sid
    payload["assigned_staff_name"] = name
    return payload


def sync_existing_contractor_tasks(staff_id: str | None = None) -> dict[str, Any]:
    """委託業者設定保存後、通常割当期間内の既存未割当タスクだけへ固定割当を反映する。"""
    start_date, end_date = contractor_assignment_range()
    property_names = _property_name_map()
    contractors = _contractors()
    if staff_id:
        contractors = [row for row in contractors if str(row.get("id")) == str(staff_id)]

    updated: list[dict[str, Any]] = []
    for staff in contractors:
        sid = str(staff.get("id") or "")
        name = str(staff.get("staff_name") or "")
        property_ids = list(staff.get("available_property_ids") or []) + list(staff.get("unchecked_property_ids") or [])
        for property_id in dict.fromkeys(str(x) for x in property_ids if x):
            property_name = property_names.get(property_id)
            if not property_name:
                continue
            tasks = (
                supabase.table("cleaning_tasks")
                .select("id,property_name,room_name,task_date,assignment_locked,assigned_staff_id,assigned_staff_ids,assigned_staff_name,assigned_staff_names")
                .eq("property_name", property_name)
                .gte("task_date", start_date)
                .lte("task_date", end_date)
                .execute()
            ).data or []
            for task in tasks:
                if task.get("assignment_locked"):
                    continue
                has_assignment = bool(
                    task.get("assigned_staff_id")
                    or task.get("assigned_staff_ids")
                    or task.get("assigned_staff_name")
                    or task.get("assigned_staff_names")
                )
                if has_assignment:
                    continue
                payload = {
                    "assigned_staff_ids": [sid],
                    "assigned_staff_names": [name],
                    "assigned_staff_id": sid,
                    "assigned_staff_name": name,
                }
                supabase.table("cleaning_tasks").update(payload).eq("id", task.get("id")).execute()
                updated.append({
                    "task_id": task.get("id"),
                    "property_name": property_name,
                    "room_name": task.get("room_name"),
                    "task_date": task.get("task_date"),
                    "staff_id": sid,
                    "staff_name": name,
                })

    return {
        "ok": True,
        "start_date": start_date,
        "end_date": end_date,
        "updated_count": len(updated),
        "updated": updated,
    }
