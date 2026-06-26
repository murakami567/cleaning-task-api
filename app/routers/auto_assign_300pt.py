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
DEFAULT_ROOM_SCORE = 60
DEFAULT_DAILY_CAPACITY = 300
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
    days: list[str] = []
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
    name = task.get("assigned_staff_name")
    if name and str(name) not in ["未割当", "未割り当て", "-"]:
        return False
    return True


def _int_or_default(value: Any, default: int) -> int:
    try:
        n = int(value)
    except Exception:
        return default
    return n if n > 0 else default


def _limit(value: Any) -> int | None:
    try:
        n = int(value)
    except Exception:
        return None
    return n if n > 0 else None


def _fetch_properties():
    res = supabase.table("properties").select("id, property_name, max_assignable_count, assignment_mode").execute()
    id_by_name = {}
    name_by_id = {}
    limit_by_id = {}
    mode_by_id = {}
    for row in res.data or []:
        pid = str(row.get("id") or "")
        name = str(row.get("property_name") or "")
        if not pid or not name:
            continue
        id_by_name[name] = pid
        name_by_id[pid] = name
        limit_by_id[pid] = _limit(row.get("max_assignable_count"))
        mode = str(row.get("assignment_mode") or "solo")
        mode_by_id[pid] = mode if mode in ["solo", "shared", "both"] else "solo"
    return id_by_name, name_by_id, limit_by_id, mode_by_id


def _fetch_room_scores():
    try:
        res = supabase.table("rooms").select("room_key, cleaning_score").execute()
    except Exception as e:
        logger.warning(f"rooms.cleaning_score unavailable, fallback score used: {e}")
        return {}
    scores = {}
    for row in res.data or []:
        key = str(row.get("room_key") or "")
        if key:
            scores[key] = _int_or_default(row.get("cleaning_score"), DEFAULT_ROOM_SCORE)
    return scores


def _task_score(task: dict[str, Any], room_scores: dict[str, int]) -> int:
    room_key = str(task.get("room_key") or "")
    if room_key and room_key in room_scores:
        return room_scores[room_key]
    return _int_or_default(task.get("load_score"), DEFAULT_ROOM_SCORE)


def _fetch_priorities() -> dict[str, dict[str, int]]:
    try:
        res = supabase.table("staff_property_priorities").select("staff_id, property_id, priority_order").execute()
    except Exception as e:
        logger.warning(f"staff_property_priorities unavailable: {e}")
        return {}
    m: dict[str, dict[str, int]] = defaultdict(dict)
    for row in res.data or []:
        sid = str(row.get("staff_id") or "")
        pid = str(row.get("property_id") or "")
        if sid and pid:
            m[sid][pid] = _int_or_default(row.get("priority_order"), 999)
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
            staff["daily_capacity_point"] = _int_or_default(staff.get("daily_capacity_point"), DEFAULT_DAILY_CAPACITY)
            staff["solo_enabled"] = staff.get("solo_enabled") is not False
            staff["shared_enabled"] = staff.get("shared_enabled") is not False
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


def _base_candidates(
    staffs: list[dict[str, Any]],
    pid: str,
    priority_map: dict[str, dict[str, int]],
    props_by_staff: dict[str, set[str]],
    ng_pairs: set[tuple[str, str]],
    mode: str,
):
    out = []
    for staff in staffs:
        sid = str(staff.get("id") or "")
        pri = _priority(staff, pid, priority_map)
        if pri is None:
            continue
        if mode == "solo" and staff.get("solo_enabled") is False:
            continue
        if mode == "shared" and staff.get("shared_enabled") is False:
            continue
        current_props = props_by_staff[sid]
        if pid not in current_props and len(current_props) >= MAX_PROPERTIES_PER_STAFF:
            continue
        if _has_ng(current_props, pid, ng_pairs):
            continue
        out.append((pri, staff))
    return out


def _sort_candidate_key(item, count_by_staff_property, used_points, props_by_staff):
    pri, staff = item
    sid = str(staff.get("id") or "")
    return (
        pri,
        count_by_staff_property[sid],
        used_points[sid],
        len(props_by_staff[sid]),
        int(staff.get("sort_order") or 999),
        str(staff.get("staff_name") or ""),
    )


def _can_take(
    sid: str,
    pid: str,
    points: int,
    staff: dict[str, Any],
    used_points: dict[str, int],
    count_by_staff_property: dict[str, dict[str, int]],
    limit: int | None,
) -> bool:
    if used_points[sid] + points > _int_or_default(staff.get("daily_capacity_point"), DEFAULT_DAILY_CAPACITY):
        return False
    if limit is not None and count_by_staff_property[sid][pid] >= limit:
        return False
    return True


def _apply_assignment(task, assignees, dry_run: bool):
    ids = [str(s.get("id")) for s in assignees]
    names = [str(s.get("staff_name") or "") for s in assignees]
    payload = {
        "assigned_staff_ids": ids,
        "assigned_staff_names": names,
        "assigned_staff_id": ids[0] if ids else None,
        "assigned_staff_name": names[0] if names else None,
    }
    if not dry_run:
        supabase.table("cleaning_tasks").update(payload).eq("id", task.get("id")).execute()
    return payload


def _assign_one_day(target_date: str, dry_run: bool) -> dict[str, Any]:
    id_by_name, name_by_id, limit_by_id, mode_by_id = _fetch_properties()
    room_scores = _fetch_room_scores()
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

    used_points: dict[str, int] = defaultdict(int)
    props_by_staff: dict[str, set[str]] = defaultdict(set)
    count_by_staff_property: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for task in tasks:
        pid = id_by_name.get(str(task.get("property_name") or ""))
        score = _task_score(task, room_scores)
        ids = task.get("assigned_staff_ids") or []
        if not isinstance(ids, list):
            ids = [ids]
        share = math.ceil(score / max(1, len([x for x in ids if x]))) if ids else score
        for sid in ids:
            sid = str(sid)
            if sid in staff_by_id and pid:
                props_by_staff[sid].add(pid)
                count_by_staff_property[sid][pid] += 1
                used_points[sid] += share

    summaries = {}
    ordered_pids = sorted(property_tasks.keys(), key=lambda p: (-len(property_tasks[p]), name_by_id.get(p, "")))

    for pid in ordered_pids:
        rows = sorted(property_tasks[pid], key=lambda t: str(t.get("room_name") or ""))
        limit = limit_by_id.get(pid)
        mode = mode_by_id.get(pid, "solo")
        total_score = sum(_task_score(t, room_scores) for t in rows)
        required_staff = max(1, math.ceil(len(rows) / limit)) if limit else 1

        summaries[pid] = {
            "property_id": pid,
            "property_name": name_by_id.get(pid) or rows[0].get("property_name"),
            "assignment_mode": mode,
            "task_count": len(rows),
            "total_score": total_score,
            "max_assignable_count": limit,
            "required_staff_count": required_staff,
            "assigned_staff_ids": [],
            "assigned_staff_names": [],
            "assigned_count": 0,
            "skipped_count": 0,
        }

        for task in rows:
            score = _task_score(task, room_scores)
            chosen = []
            actual_mode = mode

            if mode in ["solo", "both"]:
                candidates = _base_candidates(staffs, pid, priority_map, props_by_staff, ng_pairs, "solo")
                candidates = [c for c in candidates if _can_take(str(c[1].get("id")), pid, score, c[1], used_points, count_by_staff_property, limit)]
                candidates.sort(key=lambda c: _sort_candidate_key(c, count_by_staff_property, used_points, props_by_staff))
                if candidates:
                    chosen = [candidates[0][1]]
                    actual_mode = "solo"

            if not chosen and mode in ["shared", "both"]:
                candidates = _base_candidates(staffs, pid, priority_map, props_by_staff, ng_pairs, "shared")
                candidates.sort(key=lambda c: _sort_candidate_key(c, count_by_staff_property, used_points, props_by_staff))
                team_size = max(2, min(required_staff, len(candidates)))
                team = []
                for _, staff in candidates:
                    sid = str(staff.get("id"))
                    tentative_share = math.ceil(score / max(1, team_size))
                    if _can_take(sid, pid, tentative_share, staff, used_points, count_by_staff_property, limit):
                        team.append(staff)
                    if len(team) >= team_size:
                        break
                if len(team) >= 2:
                    chosen = team
                    actual_mode = "shared"

            if not chosen:
                summaries[pid]["skipped_count"] += 1
                skipped.append({"task_id": task.get("id"), "property_name": summaries[pid]["property_name"], "room_name": task.get("room_name"), "score": score, "reason": "no_candidate_or_capacity"})
                continue

            _apply_assignment(task, chosen, dry_run)
            share_points = math.ceil(score / len(chosen))
            for staff in chosen:
                sid = str(staff.get("id"))
                name = str(staff.get("staff_name") or "")
                props_by_staff[sid].add(pid)
                count_by_staff_property[sid][pid] += 1
                used_points[sid] += share_points
                if sid not in summaries[pid]["assigned_staff_ids"]:
                    summaries[pid]["assigned_staff_ids"].append(sid)
                    summaries[pid]["assigned_staff_names"].append(name)

            summaries[pid]["assigned_count"] += 1
            assigned.append({
                "task_id": task.get("id"),
                "property_name": summaries[pid]["property_name"],
                "room_name": task.get("room_name"),
                "score": score,
                "assignment_mode": actual_mode,
                "staff_ids": [str(s.get("id")) for s in chosen],
                "staff_names": [str(s.get("staff_name") or "") for s in chosen],
                "share_points": share_points,
            })

    property_summaries = list(summaries.values())
    for row in property_summaries:
        row["used_staff_count"] = len(row["assigned_staff_ids"])

    staff_summaries = []
    for staff in staffs:
        sid = str(staff.get("id"))
        staff_summaries.append({
            "staff_id": sid,
            "staff_name": staff.get("staff_name"),
            "used_points": used_points[sid],
            "daily_capacity_point": _int_or_default(staff.get("daily_capacity_point"), DEFAULT_DAILY_CAPACITY),
            "property_count": len(props_by_staff[sid]),
        })

    return {
        "date": target_date,
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
