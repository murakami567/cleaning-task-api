import os
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, Header, HTTPException

from app.db import supabase
from app.logger import get_logger
from app.routers.auto_assign_300pt import _assign_one_day, _date_range

router = APIRouter(prefix="/auto-assign", tags=["auto-assign-contractor"])
logger = get_logger(__name__)

CONTRACTOR_ROLE = "contractor"


def _today_jst():
    return (datetime.now(timezone.utc) + timedelta(hours=9)).date()


def _require_cron_key(x_cron_key: str | None) -> None:
    expected = os.getenv("CRON_SECRET") or os.getenv("AUTO_ASSIGN_CRON_KEY")
    if not expected:
        raise HTTPException(status_code=500, detail="CRON_SECRET or AUTO_ASSIGN_CRON_KEY is not configured")
    if not x_cron_key or x_cron_key != expected:
        raise HTTPException(status_code=401, detail="Invalid cron key")


def _contractors_by_property() -> dict[str, list[dict[str, Any]]]:
    """対応可能物件ID -> 有効な委託業者。シフト・能力・上限は参照しない。"""
    res = (
        supabase.table("staff_members")
        .select("id,staff_name,role,is_active,available_property_ids,unchecked_property_ids")
        .eq("role", CONTRACTOR_ROLE)
        .eq("is_active", True)
        .execute()
    )
    out: dict[str, list[dict[str, Any]]] = {}
    for staff in res.data or []:
        property_ids = list(dict.fromkeys(
            [str(x) for x in (staff.get("available_property_ids") or []) if x]
            + [str(x) for x in (staff.get("unchecked_property_ids") or []) if x]
        ))
        for property_id in property_ids:
            out.setdefault(property_id, []).append(staff)
    return out


def _property_ids_by_name() -> dict[str, str]:
    res = supabase.table("properties").select("id,property_name,is_active").execute()
    return {
        str(row.get("property_name") or "").strip(): str(row.get("id"))
        for row in res.data or []
        if row.get("id") and row.get("property_name") and row.get("is_active") is not False
    }


def _assign_contractors(target_date: str, dry_run: bool) -> list[dict[str, Any]]:
    contractors = _contractors_by_property()
    if not contractors:
        return []

    property_ids = _property_ids_by_name()
    tasks_res = (
        supabase.table("cleaning_tasks")
        .select("*")
        .eq("task_date", target_date)
        .execute()
    )
    assigned: list[dict[str, Any]] = []

    for task in tasks_res.data or []:
        # 手動固定されたタスクは上書きしない。
        if task.get("assignment_locked"):
            continue
        property_name = str(task.get("property_name") or "").strip()
        property_id = property_ids.get(property_name)
        matched = contractors.get(property_id or "", [])
        if not matched:
            continue

        # 同一物件に複数の委託業者が設定されている場合は全員を担当へ入れる。
        ids = [str(s.get("id")) for s in matched if s.get("id")]
        names = [str(s.get("staff_name") or "") for s in matched if s.get("id")]
        if not ids:
            continue
        payload = {
            "assigned_staff_ids": ids,
            "assigned_staff_names": names,
            "assigned_staff_id": ids[0],
            "assigned_staff_name": names[0] if names else "",
        }
        if not dry_run:
            supabase.table("cleaning_tasks").update(payload).eq("id", task.get("id")).execute()
        assigned.append({
            "task_id": task.get("id"),
            "property_id": property_id,
            "property_name": property_name,
            "room_name": task.get("room_name"),
            "contractor_ids": ids,
            "contractor_names": names,
        })
    return assigned


def _run_day(target_date: str, dry_run: bool) -> dict[str, Any]:
    contractor_assigned = _assign_contractors(target_date, dry_run)
    # dry-run時はDBを変更していないため通常割当との競合判定ができない。
    # 本実行時は委託物件が先に担当済みとなり、通常割当から自動的に除外される。
    normal_result = _assign_one_day(target_date, dry_run=dry_run)
    normal_result["contractor_assigned"] = contractor_assigned
    normal_result["contractor_assigned_count"] = len(contractor_assigned)
    normal_result["logic"] = "contractor_fixed_then_" + str(normal_result.get("logic") or "auto_assign")
    return normal_result


@router.post("/run")
def run_auto_assign_with_contractors(
    start_date: str = Body(...),
    end_date: str = Body(...),
    dry_run: bool = Body(True),
):
    results = [_run_day(day, dry_run=dry_run) for day in _date_range(start_date, end_date)]
    return {
        "ok": True,
        "dry_run": dry_run,
        "start_date": start_date,
        "end_date": end_date,
        "results": results,
        "total_contractor_assigned": sum(r.get("contractor_assigned_count", 0) for r in results),
        "total_assigned": sum(r.get("assigned_count", 0) for r in results),
        "total_skipped": sum(r.get("skipped_count", 0) for r in results),
    }


@router.post("/cron")
def cron_auto_assign_with_contractors(
    x_cron_key: str | None = Header(default=None, alias="X-CRON-KEY"),
):
    _require_cron_key(x_cron_key)
    today = _today_jst()
    start_date = (today + timedelta(days=3)).isoformat()
    end_date = (today + timedelta(days=7)).isoformat()
    results = [_run_day(day, dry_run=False) for day in _date_range(start_date, end_date)]
    return {
        "ok": True,
        "dry_run": False,
        "start_date": start_date,
        "end_date": end_date,
        "results": results,
        "total_contractor_assigned": sum(r.get("contractor_assigned_count", 0) for r in results),
        "total_assigned": sum(r.get("assigned_count", 0) for r in results),
        "total_skipped": sum(r.get("skipped_count", 0) for r in results),
    }
