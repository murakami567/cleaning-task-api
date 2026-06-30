import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, Header, HTTPException

from app.db import supabase
from app.logger import get_logger

router = APIRouter(prefix="/auto-assign", tags=["auto-assign"])
logger = get_logger(__name__)

DEFAULT_MAX_ASSIGNABLE_COUNT = 1
SKIP_SMALL_REMAINDER_LIMIT = 5
SKIP_SMALL_REMAINDER_COUNT = 2


def _today_jst() -> date:
    return (datetime.now(timezone.utc) + timedelta(hours=9)).date()


def _require_cron_key(x_cron_key: str | None) -> None:
    expected = os.getenv("CRON_SECRET") or os.getenv("AUTO_ASSIGN_CRON_KEY")
    if not expected:
        raise HTTPException(status_code=500, detail="CRON_SECRET or AUTO_ASSIGN_CRON_KEY is not configured")
    if not x_cron_key or x_cron_key != expected:
        raise HTTPException(status_code=401, detail="Invalid cron key")


def _date_range(start_date: str, end_date: str) -> list[str]:
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    days: list[str] = []
    cur = start
    while cur <= end:
        days.append(cur.isoformat())
        cur += timedelta(days=1)
    return days


def _int_or_default(value: Any, default: int) -> int:
    try:
        n = int(value)
    except Exception:
        return default
    return n if n > 0 else default


def _is_locked(task: dict[str, Any]) -> bool:
    return bool(task.get("assignment_locked"))


def _is_blank_assignment(task: dict[str, Any]) -> bool:
    ids = task.get("assigned_staff_ids")
    names = task.get("assigned_staff_names")
    if isinstance(ids, list) and any(ids):
        return False
    if isinstance(names, list) and any(names):
        return False
    if task.get("assigned_staff_id"):
        return False
    name = task.get("assigned_staff_name")
    if name and str(name) not in ["未割当", "未割り当て", "-"]:
        return False
    return True


def _fetch_properties():
    res = (
        supabase.table("properties")
        .select("id, property_name, max_assignable_count, sort_order, is_active")
        .execute()
    )
    id_by_name: dict[str, str] = {}
    name_by_id: dict[str, str] = {}
    limit_by_id: dict[str, int] = {}
    sort_by_id: dict[str, int] = {}

    for row in res.data or []:
        if row.get("is_active") is False:
            continue
        pid = str(row.get("id") or "")
        name = str(row.get("property_name") or "")
        if not pid or not name:
            continue
        id_by_name[name] = pid
        name_by_id[pid] = name
        limit_by_id[pid] = _int_or_default(row.get("max_assignable_count"), DEFAULT_MAX_ASSIGNABLE_COUNT)
        sort_by_id[pid] = _int_or_default(row.get("sort_order"), 999)

    return id_by_name, name_by_id, limit_by_id, sort_by_id


def _fetch_staffs(target_date: str) -> list[dict[str, Any]]:
    res = (
        supabase.table("shift_days")
        .select("*, shift_entries(*, staff_members(*))")
        .eq("shift_date", target_date)
        .execute()
    )
    out: dict[str, dict[str, Any]] = {}
    off_values = {"休み", "休日", "定休", "欠勤", "off", "OFF", "休"}
    for day in res.data or []:
        for entry in day.get("shift_entries") or []:
            status = str(entry.get("status") or "出勤")
            if status in off_values:
                continue
            staff = entry.get("staff_members") or {}
            sid = str(staff.get("id") or "")
            if not sid or staff.get("is_active") is False:
                continue
            if staff.get("role") not in ["staff", "checker", "leader", "sub_admin", "admin"]:
                continue
            out[sid] = staff
    return list(out.values())


def _fetch_tasks(target_date: str) -> list[dict[str, Any]]:
    res = (
        supabase.table("cleaning_tasks")
        .select("*")
        .eq("task_date", target_date)
        .order("property_name")
        .order("room_name")
        .execute()
    )
    return res.data or []


def _fetch_staff_property_priorities() -> dict[str, list[str]]:
    """property_id -> [staff_id...]。物件ごとの配置優先順。"""
    try:
        res = (
            supabase.table("staff_property_priorities")
            .select("staff_id, property_id, priority_order")
            .order("property_id")
            .order("priority_order")
            .execute()
        )
    except Exception as e:
        logger.warning(f"staff_property_priorities lookup skipped: {e}")
        return {}

    out: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for row in res.data or []:
        sid = str(row.get("staff_id") or "")
        pid = str(row.get("property_id") or "")
        if sid and pid:
            out[pid].append((_int_or_default(row.get("priority_order"), 999), sid))

    return {
        pid: [sid for _order, sid in sorted(rows, key=lambda x: (x[0], x[1]))]
        for pid, rows in out.items()
    }


def _apply_assignment(task, staff, dry_run: bool):
    sid = str(staff.get("id"))
    name = str(staff.get("staff_name") or "")
    payload = {
        "assigned_staff_ids": [sid],
        "assigned_staff_names": [name],
        "assigned_staff_id": sid,
        "assigned_staff_name": name,
    }
    if not dry_run:
        supabase.table("cleaning_tasks").update(payload).eq("id", task.get("id")).execute()
    return payload


def _assign_one_day(target_date: str, dry_run: bool) -> dict[str, Any]:
    id_by_name, name_by_id, limit_by_id, sort_by_id = _fetch_properties()
    priority_by_property = _fetch_staff_property_priorities()
    staffs = _fetch_staffs(target_date)
    tasks = _fetch_tasks(target_date)
    staff_by_id = {str(s.get("id")): s for s in staffs if s.get("id")}

    target_tasks = [t for t in tasks if not _is_locked(t) and _is_blank_assignment(t)]
    property_tasks: dict[str, list[dict[str, Any]]] = defaultdict(list)
    skipped = []
    assigned = []

    for task in target_tasks:
        pid = id_by_name.get(str(task.get("property_name") or ""))
        if pid:
            property_tasks[pid].append(task)
        else:
            skipped.append({
                "task_id": task.get("id"),
                "property_name": task.get("property_name"),
                "room_name": task.get("room_name"),
                "reason": "property_not_found",
            })

    summaries: dict[str, dict[str, Any]] = {}
    ordered_pids = sorted(
        property_tasks.keys(),
        key=lambda pid: (sort_by_id.get(pid, 999), name_by_id.get(pid, "")),
    )

    for pid in ordered_pids:
        rows = sorted(property_tasks[pid], key=lambda t: str(t.get("room_name") or ""))
        max_count = limit_by_id.get(pid, DEFAULT_MAX_ASSIGNABLE_COUNT)
        staff_ids = [sid for sid in priority_by_property.get(pid, []) if sid in staff_by_id]
        property_name = name_by_id.get(pid) or str(rows[0].get("property_name") or "")

        summaries[pid] = {
            "property_id": pid,
            "property_name": property_name,
            "property_sort_order": sort_by_id.get(pid, 999),
            "max_assignable_count": max_count,
            "priority_staff_ids": staff_ids,
            "priority_staff_names": [staff_by_id[sid].get("staff_name") for sid in staff_ids],
            "task_count": len(rows),
            "assigned_staff_ids": [],
            "assigned_staff_names": [],
            "assigned_count": 0,
            "skipped_count": 0,
        }

        remaining = list(rows)
        if not staff_ids:
            for task in remaining:
                summaries[pid]["skipped_count"] += 1
                skipped.append({
                    "task_id": task.get("id"),
                    "property_name": property_name,
                    "room_name": task.get("room_name"),
                    "reason": "no_priority_staff",
                })
            continue

        for priority_index, sid in enumerate(staff_ids, start=1):
            if not remaining:
                break

            if max_count >= SKIP_SMALL_REMAINDER_LIMIT and len(remaining) <= SKIP_SMALL_REMAINDER_COUNT:
                for task in remaining:
                    summaries[pid]["skipped_count"] += 1
                    skipped.append({
                        "task_id": task.get("id"),
                        "property_name": property_name,
                        "room_name": task.get("room_name"),
                        "reason": "small_remainder_skipped",
                        "remaining_count": len(remaining),
                        "max_assignable_count": max_count,
                    })
                remaining = []
                break

            staff = staff_by_id[sid]
            take_count = min(max_count, len(remaining))
            take_rows = remaining[:take_count]
            remaining = remaining[take_count:]

            for task in take_rows:
                _apply_assignment(task, staff, dry_run)
                if sid not in summaries[pid]["assigned_staff_ids"]:
                    summaries[pid]["assigned_staff_ids"].append(sid)
                    summaries[pid]["assigned_staff_names"].append(str(staff.get("staff_name") or ""))
                summaries[pid]["assigned_count"] += 1
                assigned.append({
                    "task_id": task.get("id"),
                    "property_id": pid,
                    "property_name": property_name,
                    "room_name": task.get("room_name"),
                    "staff_id": sid,
                    "staff_name": staff.get("staff_name"),
                    "priority_order": priority_index,
                    "max_assignable_count": max_count,
                })

        for task in remaining:
            summaries[pid]["skipped_count"] += 1
            skipped.append({
                "task_id": task.get("id"),
                "property_name": property_name,
                "room_name": task.get("room_name"),
                "reason": "priority_staff_capacity_exhausted",
            })

    property_summaries = list(summaries.values())
    for row in property_summaries:
        row["used_staff_count"] = len(row["assigned_staff_ids"])

    staff_summaries = []
    assigned_by_staff: dict[str, int] = defaultdict(int)
    for row in assigned:
        assigned_by_staff[str(row.get("staff_id"))] += 1
    for staff in staffs:
        sid = str(staff.get("id"))
        staff_summaries.append({
            "staff_id": sid,
            "staff_name": staff.get("staff_name"),
            "assigned_count": assigned_by_staff[sid],
        })

    return {
        "date": target_date,
        "logic": "simple_property_priority_v1",
        "staff_count": len(staffs),
        "task_count": len(tasks),
        "locked_count": len([t for t in tasks if _is_locked(t)]),
        "already_assigned_count": len([t for t in tasks if not _is_blank_assignment(t)]),
        "target_unassigned_count": len(target_tasks),
        "property_summaries": property_summaries,
        "staff_summaries": staff_summaries,
        "assigned_count": len(assigned),
        "skipped_count": len(skipped),
        "assigned": assigned,
        "skipped": skipped,
    }


@router.post("/run")
def run_auto_assign(start_date: str = Body(...), end_date: str = Body(...), dry_run: bool = Body(True)):
    results = [_assign_one_day(day, dry_run=dry_run) for day in _date_range(start_date, end_date)]
    return {
        "ok": True,
        "dry_run": dry_run,
        "start_date": start_date,
        "end_date": end_date,
        "results": results,
        "total_assigned": sum(r["assigned_count"] for r in results),
        "total_skipped": sum(r["skipped_count"] for r in results),
    }


@router.post("/cron")
def cron_auto_assign(x_cron_key: str | None = Header(default=None, alias="X-CRON-KEY")):
    _require_cron_key(x_cron_key)
    today = _today_jst()
    start_date = (today + timedelta(days=3)).isoformat()
    end_date = (today + timedelta(days=7)).isoformat()
    results = [_assign_one_day(day, dry_run=False) for day in _date_range(start_date, end_date)]
    return {
        "ok": True,
        "dry_run": False,
        "start_date": start_date,
        "end_date": end_date,
        "results": results,
        "total_assigned": sum(r["assigned_count"] for r in results),
        "total_skipped": sum(r["skipped_count"] for r in results),
    }


@router.post("/tasks/{task_id}/lock")
def lock_task_assignment(task_id: str, locked: bool = Body(True, embed=True)):
    try:
        res = supabase.table("cleaning_tasks").update({"assignment_locked": locked}).eq("id", task_id).execute()
    except Exception as e:
        logger.error(f"lock_task_assignment failed: task_id={task_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="assignment lock update failed")
    return {"ok": True, "task_id": task_id, "assignment_locked": locked, "data": res.data or []}
