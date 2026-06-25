from datetime import date
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from app.db import supabase
from app.logger import get_logger

router = APIRouter(tags=["compat"])
logger = get_logger(__name__)


def _safe_data(res: Any):
    return res.data if getattr(res, "data", None) is not None else []


def _month_range(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month + 1, 1)
    return start, end


def _count_working_entries(entries: list[dict[str, Any]]) -> int:
    off_values = {"休み", "定休", "欠勤", "off", "OFF", "休"}
    return len([e for e in entries if str(e.get("status") or "") not in off_values])


def _date_key(value: Any) -> str:
    return str(value or "")[:10]


def _fetch_all_cleaning_tasks_for_count() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page_size = 1000
    start = 0

    while True:
        end = start + page_size - 1
        res = (
            supabase.table("cleaning_tasks")
            .select("id, task_date, checkout_date, status, load_score")
            .order("task_date")
            .range(start, end)
            .execute()
        )
        batch = res.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
        start += page_size

    return rows


@router.get("/properties")
def get_properties():
    try:
        res = (
            supabase.table("properties")
            .select("*")
            .order("sort_order")
            .order("property_name")
            .execute()
        )
        return _safe_data(res)
    except Exception as e:
        logger.error(f"compat get_properties failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="properties fetch failed")


@router.get("/rooms")
def get_rooms(property_id: str | None = None):
    try:
        q = supabase.table("rooms").select("*")
        if property_id:
            q = q.eq("property_id", property_id)
        res = q.order("room_sort_order").order("room_name").execute()
        return _safe_data(res)
    except Exception as e:
        logger.error(f"compat get_rooms failed: property_id={property_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="rooms fetch failed")


@router.get("/shifts")
def get_shifts(shift_date: str | None = None):
    try:
        q = supabase.table("shift_days").select("*, shift_entries(*, staff_members(*))")
        if shift_date:
            q = q.eq("shift_date", shift_date)
        else:
            q = q.eq("shift_date", date.today().isoformat())
        res = q.order("shift_date").execute()
        return _safe_data(res)
    except Exception as e:
        logger.error(f"compat get_shifts failed: shift_date={shift_date} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="shifts fetch failed")


@router.post("/shifts/create_day")
def create_shift_day(shift_date: str = Body(...), note: str = Body("")):
    return get_or_create_shift_day(shift_date=shift_date, note=note)


@router.post("/shifts/get_or_create_day")
def get_or_create_shift_day(shift_date: str = Body(...), note: str = Body("")):
    try:
        existing = (
            supabase.table("shift_days")
            .select("*")
            .eq("shift_date", shift_date)
            .limit(1)
            .execute()
        )
        if existing.data:
            return existing.data[0]

        res = (
            supabase.table("shift_days")
            .insert({"shift_date": shift_date, "note": note})
            .execute()
        )
        if not res.data:
            raise HTTPException(status_code=500, detail="shift day creation failed")
        return res.data[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"compat get_or_create_shift_day failed: shift_date={shift_date} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="shift day creation failed")


@router.post("/shifts/upsert_entry")
def upsert_shift_entry(
    shift_day_id: str = Body(...),
    staff_id: str = Body(...),
    status: str = Body(...),
    start_time: str | None = Body(None),
    end_time: str | None = Body(None),
    assigned_area: str | None = Body(None),
    note: str | None = Body(None),
):
    try:
        existing = (
            supabase.table("shift_entries")
            .select("*")
            .eq("shift_day_id", shift_day_id)
            .eq("staff_id", staff_id)
            .limit(1)
            .execute()
        )
        payload = {
            "shift_day_id": shift_day_id,
            "staff_id": staff_id,
            "status": status,
            "start_time": start_time,
            "end_time": end_time,
            "assigned_area": assigned_area or "",
            "note": note or "",
        }
        if existing.data:
            res = (
                supabase.table("shift_entries")
                .update(payload)
                .eq("id", existing.data[0].get("id"))
                .execute()
            )
        else:
            res = supabase.table("shift_entries").insert(payload).execute()
        return {"ok": True, "data": _safe_data(res)}
    except Exception as e:
        logger.error(
            f"compat upsert_shift_entry failed: shift_day_id={shift_day_id} staff_id={staff_id} {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="shift entry upsert failed")


@router.get("/staff-schedules")
def get_staff_schedules(shift_date: str):
    try:
        res = (
            supabase.table("shift_days")
            .select("*, shift_entries(*, staff_members(*))")
            .eq("shift_date", shift_date)
            .execute()
        )
        day = res.data[0] if res.data else None
        if not day:
            return {"shift_date": shift_date, "entries": [], "shift_entries": []}
        return {
            "shift_date": shift_date,
            "id": day.get("id"),
            "entries": day.get("shift_entries") or [],
            "shift_entries": day.get("shift_entries") or [],
        }
    except Exception as e:
        logger.error(f"compat get_staff_schedules failed: shift_date={shift_date} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="staff schedules fetch failed")


@router.get("/shift-board")
def get_shift_board(year: int, month: int):
    try:
        start, end = _month_range(year, month)
        start_iso = start.isoformat()
        end_iso = end.isoformat()

        staff_res = (
            supabase.table("staff_members")
            .select("id, staff_code, staff_name, role, is_active, sort_order")
            .eq("is_active", True)
            .in_("role", ["staff", "checker", "leader", "sub_admin", "admin"])
            .order("sort_order")
            .order("staff_name")
            .execute()
        )

        shift_res = (
            supabase.table("shift_days")
            .select("*, shift_entries(*, staff_members(*))")
            .gte("shift_date", start_iso)
            .lt("shift_date", end_iso)
            .order("shift_date")
            .execute()
        )

        # DB側の日付比較に頼らず、タスク管理と同じ task_date をPython側で月内判定する。
        # これにより task_date の型・形式差分で0件になる事故を避ける。
        task_rows = _fetch_all_cleaning_tasks_for_count()

        cleaning_counts: dict[str, int] = {}
        workload_score: dict[str, int] = {}
        excluded_statuses = {"CXL", "キャンセル", "cancelled", "Cancelled"}

        for row in task_rows:
            status = str(row.get("status") or "")
            if status in excluded_statuses:
                continue

            d = _date_key(row.get("task_date"))
            if not d or d < start_iso or d >= end_iso:
                continue

            cleaning_counts[d] = cleaning_counts.get(d, 0) + 1
            try:
                score = int(row.get("load_score") or 0)
            except Exception:
                score = 0
            workload_score[d] = workload_score.get(d, 0) + score

        attendance_counts: dict[str, int] = {}
        for day in shift_res.data or []:
            d = _date_key(day.get("shift_date"))
            entries = day.get("shift_entries") if isinstance(day.get("shift_entries"), list) else []
            attendance_counts[d] = _count_working_entries(entries)

        workload: dict[str, float] = {}
        for d, count in cleaning_counts.items():
            attendance = attendance_counts.get(d, 0)
            workload[d] = round(count / attendance, 1) if attendance > 0 else 0

        return {
            "year": year,
            "month": month,
            "staffs": staff_res.data or [],
            "days": shift_res.data or [],
            "cleaning_counts": cleaning_counts,
            "attendance_counts": attendance_counts,
            "workload": workload,
            "workload_score": workload_score,
            "debug": {
                "all_task_rows": len(task_rows),
                "counted_days": cleaning_counts,
            },
        }
    except Exception as e:
        logger.error(f"compat get_shift_board failed: year={year} month={month} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="shift board fetch failed")
