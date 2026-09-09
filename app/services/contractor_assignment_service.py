from typing import Any

from app.db import supabase


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
    """新規清掃タスク用。対象物件なら委託業者を担当としてセットする。"""
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
    """委託業者設定保存後、対象物件の既存未割当タスクへ固定割当を反映する。"""
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

    return {"ok": True, "updated_count": len(updated), "updated": updated}
