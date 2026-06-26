import math
import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, Header, HTTPException

from app.db import supabase
from app.logger import get_logger

router = APIRouter(prefix="/auto-assign", tags=["auto-assign"])
logger = get_logger(__name__)

MAX_PROPERTIES_PER_STAFF = 2
FALLBACK_PRIORITY_UNCHECKED = 100
FALLBACK_PRIORITY_AVAILABLE = 1000


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
    days = []
    cur = start
    while cur <= end:
        days.append(cur.isoformat())
        cur += timedelta(days=1)
    return days


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
    single_name = task.get("assigned_staff_name")
    if single_name and str(single_name) not in ["未割当", "未割り当て", "-"]:
        return False
    return True


def _limit(value: Any) -> int | None:
    try:
        n = int(value)
    except Exception:
        return None
    return n if n > 0 else None


def _fetch_properties():
    res = supabase.table("properties").select("id, property_name, max_assignable_count").execute()
    id_by_name = {}
    name_by_id = {}
    limit_by_id = {}
    for row in res.data or []:
        pid = str(row.get("id") or "")
        name = str(row.get("property_name") or "")
        if pid and name:
            id_by_name[name] = pid
            name_by_id[pid] = name
            limit_by_id[pid] = _limit(row.get("max_assignable_count"))
    return id_by_name, name_by_id, limit_by_id


def _fetch_priorities() -> dict[str, dict[str, int]]:
    try:
        res = supabase.table("staff_property_priorities").select("staff_id, property_id, priority_order").execute()
    except Exception as e:
        logger.warning(f"staff_property_priorities unavailable: {e}")
        return {}
    m: dict[str, dict[str, int]] = defaultdict(dict)
    for row in res.data or []:
        staff_id = str(row.get("staff_id") or "")
        property_id = str(row.get("property_id") or "")
        if not staff_id or not property_id:
            continue
        try:
            order = int(row.get("priority_order") or 999)
        except Exception:
            order = 999
        m[staff_id][property_id] = order
    return m


def _fetch_staffs(target_date: str) -> list[dict[str, Any]]:
    res = (
        supabase.table("shift_days")
        .select("*, shift_entries(*, staff_members(*))")
        .eq("shift_date", target_date)
        .execute()
    )
    out = {}
    for day in res.data or []:
        for entry in day.get("shift_entries") or []:
            status = str(entry.get("status") or "出勤")
            if status in ["休み", "休日", "欠勤", "off", "OFF", "休"]:
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


def _fetch_ng_pairs() -> set[tuple[str, str]]:
    for table in ["property_ng_pairs", "property_movement_ng_pairs", "ng_property_pairs"]:
        try:
            res = supabase.table(table).select("*").execute()
        except Exception:
            continue
        pairs = set()
        for row in res.data or []:
            a = str(row.get("property_id_a") or row.get("from_property_id") or row.get("property_a_id") or "")
            b = str(row.get("property_id_b") or row.get("to_property_id") or row.get("property_b_id") or "")
            if a and b:
                pairs.add((a, b))
                pairs.add((b, a))
        return pairs
    return set()


def _has_ng(current: set[str], pid: str, ng_pairs: set[tuple[str, str]]) -> bool:
    return any((x, pid) in ng_pairs for x in current)


def _priority(staff: dict[str, Any], pid: str, priority_map: dict[str, dict[str, int]]) -> int | None:
    sid = str(staff.get("id") or "")
    if priority_map.get(sid, {}).get(pid) is not None:
        return priority_map[sid][pid]
    unchecked = [str(x) for x in (staff.get("unchecked_property_ids") or [])]
    available = [str(x) for x in (staff.get("available_property_ids") or [])]
    if pid in unchecked:
        return FALLBACK_PRIORITY_UNCHECKED
    if pid in available:
        return FALLBACK_PRIORITY_AVAILABLE
    return None


def _required_staff(task_count: int, limit: int | None) -> int:
    if task_count <= 0:
        return 0
    if limit is None:
        return 1
    return max(1, math.ceil(task_count / limit))


def _assign_one_day(target_date: str, dry_run: bool) -> dict[str, Any]:
    id_by_name, name_by_id, limit_by_id = _fetch_properties()
    priority_map = _fetch_priorities()
    ng_pairs = _fetch_ng_pairs()
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
            skipped.append({"task_id": task.get("id"), "reason": "property_not_found", "property_name": task.get("property_name")})

    load_by_staff: dict[str, int] = defaultdict(int)
    props_by_staff: dict[str, set[str]] = defaultdict(set)
    count_by_staff_property: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for task in tasks:
        pid = id_by_name.get(str(task.get("property_name") or ""))
        ids = task.get("assigned_staff_ids") or []
        if not isinstance(ids, list):
            ids = [ids]
        for sid in ids:
            sid = str(sid)
            if sid in staff_by_id and pid:
                props_by_staff[sid].add(pid)
                count_by_staff_property[sid][pid] += 1
                load_by_staff[sid] += 1

    summaries = {}
    ordered_pids = sorted(property_tasks.keys(), key=lambda p: (-len(property_tasks[p]), name_by_id.get(p, "")))

    for pid in ordered_pids:
        rows = sorted(property_tasks[pid], key=lambda t: str(t.get("room_name") or ""))
        limit = limit_by_id.get(pid)
        summaries[pid] = {
            "property_id": pid,
            "property_name": name_by_id.get(pid) or rows[0].get("property_name"),
            "task_count": len(rows),
            "max_assignable_count": limit,
            "required_staff_count": _required_staff(len(rows), limit),
            "assigned_staff_ids": [],
            "assigned_staff_names": [],
            "assigned_count": 0,
            "skipped_count": 0,
        }

        for task in rows:
            candidates = []
            for staff in staffs:
                sid = str(staff.get("id") or "")
                pri = _priority(staff, pid, priority_map)
                if pri is None:
                    continue
                current_props = props_by_staff[sid]
                if pid not in current_props and len(current_props) >= MAX_PROPERTIES_PER_STAFF:
                    continue
                if _has_ng(current_props, pid, ng_pairs):
                    continue
                if limit is not None and count_by_staff_property[sid][pid] >= limit:
                    continue
                candidates.append((pri, count_by_staff_property[sid][pid], load_by_staff[sid], len(current_props), int(staff.get("sort_order") or 999), str(staff.get("staff_name") or ""), staff))

            if not candidates:
                summaries[pid]["skipped_count"] += 1
                skipped.append({"task_id": task.get("id"), "property_name": summaries[pid]["property_name"], "room_name": task.get("room_name"), "reason": "no_candidate"})
                continue

            candidates.sort(key=lambda x: (x[0], x[1], x[2], x[3], x[4], x[5]))
            pri, _, _, _, _, _, staff = candidates[0]
            sid = str(staff.get("id"))
            name = str(staff.get("staff_name") or "")
            props_by_staff[sid].add(pid)
            count_by_staff_property[sid][pid] += 1
            load_by_staff[sid] += 1

            payload = {"assigned_staff_ids": [sid], "assigned_staff_names": [name], "assigned_staff_id": sid, "assigned_staff_name": name}
            if not dry_run:
                supabase.table("cleaning_tasks").update(payload).eq("id", task.get("id")).execute()

            if sid not in summaries[pid]["assigned_staff_ids"]:
                summaries[pid]["assigned_staff_ids"].append(sid)
                summaries[pid]["assigned_staff_names"].append(name)
            summaries[pid]["assigned_count"] += 1

            assigned.append({"task_id": task.get("id"), "property_name": summaries[pid]["property_name"], "room_name": task.get("room_name"), "staff_id": sid, "staff_name": name, "priority_order": pri, "staff_property_assigned_count": count_by_staff_property[sid][pid]})

    property_summaries = list(summaries.values())
    for row in property_summaries:
        row["used_staff_count"] = len(row["assigned_staff_ids"])

    return {"date": target_date, "staff_count": len(staffs), "task_count": len(tasks), "locked_count": len([t for t in tasks if _is_locked(t)]), "already_assigned_count": len([t for t in tasks if not _is_blank_assignment(t)]), "target_unassigned_count": len(target_tasks), "property_summaries": property_summaries, "assigned_count": len(assigned), "skipped_count": len(skipped), "assigned": assigned, "skipped": skipped}


@router.post("/run")
def run_auto_assign(start_date: str = Body(...), end_date: str = Body(...), dry_run: bool = Body(True)):
    results = [_assign_one_day(day, dry_run=dry_run) for day in _date_range(start_date, end_date)]
    return {"ok": True, "dry_run": dry_run, "start_date": start_date, "end_date": end_date, "results": results, "total_assigned": sum(r["assigned_count"] for r in results), "total_skipped": sum(r["skipped_count"] for r in results)}


@router.post("/cron")
def cron_auto_assign(x_cron_key: str | None = Header(default=None, alias="X-CRON-KEY")):
    _require_cron_key(x_cron_key)
    today = _today_jst()
    start_date = (today + timedelta(days=3)).isoformat()
    end_date = (today + timedelta(days=7)).isoformat()
    results = [_assign_one_day(day, dry_run=False) for day in _date_range(start_date, end_date)]
    return {"ok": True, "dry_run": False, "start_date": start_date, "end_date": end_date, "results": results, "total_assigned": sum(r["assigned_count"] for r in results), "total_skipped": sum(r["skipped_count"] for r in results)}


@router.post("/tasks/{task_id}/lock")
def lock_task_assignment(task_id: str, locked: bool = Body(True, embed=True)):
    try:
        res = supabase.table("cleaning_tasks").update({"assignment_locked": locked}).eq("id", task_id).execute()
    except Exception as e:
        logger.error(f"lock_task_assignment failed: task_id={task_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="assignment lock update failed")
    return {"ok": True, "task_id": task_id, "assignment_locked": locked, "data": res.data}
