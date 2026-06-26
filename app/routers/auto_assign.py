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


def _is_blank_assignment(task: dict[str, Any]) -> bool:
    ids = task.get("assigned_staff_ids")
    names = task.get("assigned_staff_names")
    single_id = task.get("assigned_staff_id")
    single_name = task.get("assigned_staff_name")

    if isinstance(ids, list) and len([x for x in ids if x]) > 0:
        return False
    if isinstance(names, list) and len([x for x in names if x]) > 0:
        return False
    if single_id:
        return False
    if single_name and str(single_name) not in ["未割当", "未割り当て", "-"]:
        return False
    return True


def _is_locked(task: dict[str, Any]) -> bool:
    return bool(task.get("assignment_locked"))


def _task_load(task: dict[str, Any]) -> int:
    try:
        score = int(task.get("load_score") or 0)
    except Exception:
        score = 0
    return score if score > 0 else 1


def _normalize_property_limit(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        n = int(value)
    except Exception:
        return None
    return n if n > 0 else None


def _fetch_properties() -> tuple[dict[str, str], dict[str, str], dict[str, int | None]]:
    res = supabase.table("properties").select("id, property_name, max_assignable_count").execute()
    by_name: dict[str, str] = {}
    name_by_id: dict[str, str] = {}
    max_by_id: dict[str, int | None] = {}
    for row in res.data or []:
        pid = str(row.get("id") or "")
        name = str(row.get("property_name") or "")
        if pid and name:
            by_name[name] = pid
            name_by_id[pid] = name
            max_by_id[pid] = _normalize_property_limit(row.get("max_assignable_count"))
    return by_name, name_by_id, max_by_id


def _fetch_shift_staff(target_date: str) -> list[dict[str, Any]]:
    shift_res = (
        supabase.table("shift_days")
        .select("*, shift_entries(*, staff_members(*))")
        .eq("shift_date", target_date)
        .execute()
    )
    if not shift_res.data:
        return []

    staffs: list[dict[str, Any]] = []
    for shift_day in shift_res.data or []:
        for entry in shift_day.get("shift_entries") or []:
            status = str(
                entry.get("status")
                or entry.get("shift_status")
                or entry.get("work_status")
                or "出勤"
            )
            if status in ["休み", "休日", "欠勤", "off", "OFF", "休"]:
                continue

            staff = entry.get("staff_members") or {}
            if not staff or staff.get("is_active") is False:
                continue
            if staff.get("role") not in ["staff", "checker", "leader", "sub_admin", "admin"]:
                continue
            staffs.append(staff)

    dedup: dict[str, dict[str, Any]] = {}
    for staff in staffs:
        sid = str(staff.get("id") or "")
        if sid:
            dedup[sid] = staff
    return list(dedup.values())


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
    table_names = ["property_ng_pairs", "property_movement_ng_pairs", "ng_property_pairs"]
    for table_name in table_names:
        try:
            res = supabase.table(table_name).select("*").execute()
        except Exception:
            continue

        pairs: set[tuple[str, str]] = set()
        for row in res.data or []:
            a = str(row.get("property_id_a") or row.get("from_property_id") or row.get("property_a_id") or "")
            b = str(row.get("property_id_b") or row.get("to_property_id") or row.get("property_b_id") or "")
            if a and b:
                pairs.add((a, b))
                pairs.add((b, a))
        return pairs
    return set()


def _has_ng(existing_property_ids: set[str], property_id: str, ng_pairs: set[tuple[str, str]]) -> bool:
    return any((existing_id, property_id) in ng_pairs for existing_id in existing_property_ids)


def _eligible_staff(staffs: list[dict[str, Any]], property_id: str) -> list[tuple[int, dict[str, Any]]]:
    eligible: list[tuple[int, dict[str, Any]]] = []
    for staff in staffs:
        unchecked = [str(x) for x in (staff.get("unchecked_property_ids") or [])]
        available = [str(x) for x in (staff.get("available_property_ids") or [])]
        if property_id in unchecked:
            eligible.append((0, staff))
        elif property_id in available:
            eligible.append((1, staff))
    return eligible


def _assign_one_day(target_date: str, dry_run: bool) -> dict[str, Any]:
    property_id_by_name, _, property_max_by_id = _fetch_properties()
    ng_pairs = _fetch_ng_pairs()
    staffs = _fetch_shift_staff(target_date)
    tasks = _fetch_tasks(target_date)

    unlocked_unassigned = [t for t in tasks if not _is_locked(t) and _is_blank_assignment(t)]
    locked_count = len([t for t in tasks if _is_locked(t)])
    already_assigned_count = len([t for t in tasks if not _is_blank_assignment(t)])

    load_by_staff: dict[str, int] = defaultdict(int)
    properties_by_staff: dict[str, set[str]] = defaultdict(set)
    property_task_count_by_staff: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    assigned: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    # 既存割当を負荷・物件数・物件別件数に含める。ロック済みや手動割当を自動割当が壊さないため。
    staff_by_id = {str(s.get("id")): s for s in staffs if s.get("id")}
    for task in tasks:
        ids = task.get("assigned_staff_ids") or []
        if not isinstance(ids, list):
            ids = [ids]
        property_id = property_id_by_name.get(str(task.get("property_name") or ""))
        for sid in ids:
            sid = str(sid)
            if sid in staff_by_id:
                load_by_staff[sid] += _task_load(task)
                if property_id:
                    properties_by_staff[sid].add(property_id)
                    property_task_count_by_staff[sid][property_id] += 1

    for task in unlocked_unassigned:
        property_name = str(task.get("property_name") or "")
        property_id = property_id_by_name.get(property_name)
        if not property_id:
            skipped.append({"task_id": task.get("id"), "property_name": property_name, "reason": "property_not_found"})
            continue

        property_limit = property_max_by_id.get(property_id)
        candidates = []
        for priority, staff in _eligible_staff(staffs, property_id):
            sid = str(staff.get("id"))
            current_props = properties_by_staff[sid]
            if property_id not in current_props and len(current_props) >= MAX_PROPERTIES_PER_STAFF:
                continue
            if _has_ng(current_props, property_id, ng_pairs):
                continue
            if property_limit is not None and property_task_count_by_staff[sid][property_id] >= property_limit:
                continue
            candidates.append((priority, load_by_staff[sid], property_task_count_by_staff[sid][property_id], len(current_props), str(staff.get("sort_order") or 999), staff))

        if not candidates:
            skipped.append({
                "task_id": task.get("id"),
                "property_name": property_name,
                "reason": "no_eligible_staff_or_property_limit",
                "property_max_assignable_count": property_limit,
            })
            continue

        candidates.sort(key=lambda x: (x[0], x[1], x[2], x[3], x[4], str(x[5].get("staff_name") or "")))
        _, _, _, _, _, selected = candidates[0]
        sid = str(selected.get("id"))
        sname = str(selected.get("staff_name") or "")
        load_by_staff[sid] += _task_load(task)
        properties_by_staff[sid].add(property_id)
        property_task_count_by_staff[sid][property_id] += 1

        payload = {
            "assigned_staff_ids": [sid],
            "assigned_staff_names": [sname],
            "assigned_staff_id": sid,
            "assigned_staff_name": sname,
        }
        if not dry_run:
            supabase.table("cleaning_tasks").update(payload).eq("id", task.get("id")).execute()

        assigned.append({
            "task_id": task.get("id"),
            "property_name": property_name,
            "room_name": task.get("room_name"),
            "staff_id": sid,
            "staff_name": sname,
            "property_max_assignable_count": property_limit,
            "staff_property_assigned_count": property_task_count_by_staff[sid][property_id],
            "priority": "unchecked" if property_id in [str(x) for x in (selected.get("unchecked_property_ids") or [])] else "available",
        })

    return {
        "date": target_date,
        "staff_count": len(staffs),
        "task_count": len(tasks),
        "locked_count": locked_count,
        "already_assigned_count": already_assigned_count,
        "target_unassigned_count": len(unlocked_unassigned),
        "assigned_count": len(assigned),
        "skipped_count": len(skipped),
        "assigned": assigned,
        "skipped": skipped,
    }


@router.post("/run")
def run_auto_assign(
    start_date: str = Body(...),
    end_date: str = Body(...),
    dry_run: bool = Body(True),
):
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
    logger.info(
        f"auto_assign cron completed: start={start_date} end={end_date} assigned={sum(r['assigned_count'] for r in results)} skipped={sum(r['skipped_count'] for r in results)}"
    )
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
        res = (
            supabase.table("cleaning_tasks")
            .update({"assignment_locked": locked})
            .eq("id", task_id)
            .execute()
        )
    except Exception as e:
        logger.error(f"lock_task_assignment failed: task_id={task_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="assignment_locked column may be missing. Please run migration first.")
    return {"ok": True, "task_id": task_id, "assignment_locked": locked, "data": res.data}
