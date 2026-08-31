from datetime import date
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.db import supabase
from app.logger import get_logger
from app.services.auth_service import require_shift_worklog_write

router = APIRouter(tags=["compat"])
logger = get_logger(__name__)

SHIFT_DAY_SELECT_MINIMAL = (
    "id, shift_date, note, "
    "shift_entries(id, shift_day_id, staff_id, status, start_time, end_time, assigned_area, note, "
    "staff_members(id, staff_code, staff_name, role, is_active, sort_order, available_property_ids))"
)

SHIFT_BOARD_SELECT_MINIMAL = (
    "id, shift_date, note, "
    "shift_entries(id, shift_day_id, staff_id, status, start_time, end_time, assigned_area, note, "
    "staff_members(id, staff_code, staff_name, role, is_active, sort_order))"
)


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


def _fetch_cleaning_tasks_for_month_count(start_iso: str, end_iso: str) -> list[dict[str, Any]]:
    """シフト表の総清掃数用。Supabase の1000件上限を避けて対象月を全件取得する。"""
    rows: list[dict[str, Any]] = []
    page_size = 1000
    offset = 0

    while True:
        try:
            query = (
                supabase.table("cleaning_tasks")
                .select("id, task_date, checkout_date, status, load_score")
                .gte("task_date", start_iso)
                .lt("task_date", end_iso)
                .order("task_date")
                .order("id")
                .range(offset, offset + page_size - 1)
            )
            res = query.execute()
        except Exception as e:
            logger.warning(f"cleaning task month count with load_score failed: offset={offset} {e}")
            res = (
                supabase.table("cleaning_tasks")
                .select("id, task_date, checkout_date, status")
                .gte("task_date", start_iso)
                .lt("task_date", end_iso)
                .order("task_date")
                .order("id")
                .range(offset, offset + page_size - 1)
                .execute()
            )

        batch = res.data or []
        rows.extend(batch)

        if len(batch) < page_size:
            break
        offset += page_size

    logger.info(
        f"shift board cleaning task fetch: start={start_iso} end={end_iso} rows={len(rows)}"
    )
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
        target_date = shift_date or date.today().isoformat()
        res = (
            supabase.table("shift_days")
            .select(SHIFT_DAY_SELECT_MINIMAL)
            .eq("shift_date", target_date)
            .limit(1)
            .execute()
        )
        rows = _safe_data(res)
        logger.info(f"compat get_shifts: shift_date={target_date} days={len(rows)}")
        return rows
    except Exception as e:
        logger.error(f"compat get_shifts failed: shift_date={shift_date} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="shifts fetch failed")


@router.post("/shifts/create_day")
def create_shift_day(shift_date: str = Body(...), note: str = Body(""), current_user: dict = Depends(require_shift_worklog_write)):
    return get_or_create_shift_day(shift_date=shift_date, note=note, current_user=current_user)


@router.post("/shifts/get_or_create_day")
def get_or_create_shift_day(shift_date: str = Body(...), note: str = Body(""), current_user: dict = Depends(require_shift_worklog_write)):
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

        res = supabase.table("shift_days").insert({"shift_date": shift_date, "note": note}).execute()
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
    current_user: dict = Depends(require_shift_worklog_write),
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
            res = supabase.table("shift_entries").update(payload).eq("id", existing.data[0].get("id")).execute()
        else:
            res = supabase.table("shift_entries").insert(payload).execute()
        return {"ok": True, "data": _safe_data(res)}
    except Exception as e:
        logger.error(f"compat upsert_shift_entry failed: shift_day_id={shift_day_id} staff_id={staff_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="shift entry upsert failed")


@router.get("/staff-schedules")
def get_staff_schedules(shift_date: str):
    try:
        res = (
            supabase.table("shift_days")
            .select(SHIFT_DAY_SELECT_MINIMAL)
            .eq("shift_date", shift_date)
            .limit(1)
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


@router.post("/staff-schedules/upsert")
def upsert_staff_schedule(body: dict[str, Any] = Body(...), current_user: dict = Depends(require_shift_worklog_write)):
    """旧フロント互換。日付から shift_day を作成し、対象スタッフの予定を更新する。"""
    shift_date = _date_key(body.get("shift_date") or body.get("date") or body.get("work_date"))
    staff_id = str(body.get("staff_id") or body.get("user_id") or "").strip()
    if not shift_date:
        raise HTTPException(status_code=400, detail="shift_date is required")
    if not staff_id:
        raise HTTPException(status_code=400, detail="staff_id is required")

    status = str(body.get("status") or body.get("schedule_status") or "出勤")
    start_time = body.get("start_time")
    end_time = body.get("end_time")
    assigned_area = body.get("assigned_area") or body.get("area") or ""
    note = body.get("note") or ""

    try:
        day = get_or_create_shift_day(shift_date=shift_date, note="", current_user=current_user)
        shift_day_id = str(day.get("id") or "")
        if not shift_day_id:
            raise HTTPException(status_code=500, detail="shift day id missing")

        result = upsert_shift_entry(
            shift_day_id=shift_day_id,
            staff_id=staff_id,
            status=status,
            start_time=start_time,
            end_time=end_time,
            assigned_area=assigned_area,
            note=note,
            current_user=current_user,
        )
        logger.info(
            f"compat upsert_staff_schedule: shift_date={shift_date} staff_id={staff_id} status={status}"
        )
        return {"ok": True, "shift_date": shift_date, "staff_id": staff_id, "data": result.get("data", [])}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"compat upsert_staff_schedule failed: shift_date={shift_date} staff_id={staff_id} {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="staff schedule upsert failed")


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
            .select(SHIFT_BOARD_SELECT_MINIMAL)
            .gte("shift_date", start_iso)
            .lt("shift_date", end_iso)
            .order("shift_date")
            .execute()
        )

        task_rows = _fetch_cleaning_tasks_for_month_count(start_iso, end_iso)

        cleaning_counts: dict[str, int] = {}
        workload_score: dict[str, int] = {}
        excluded_statuses = {"cxl", "キャンセル", "cancelled", "canceled"}

        for row in task_rows:
            status = str(row.get("status") or "").strip().lower()
            if status in excluded_statuses:
                continue

            d = _date_key(row.get("task_date"))
            if not d or d < start_iso or d >= end_iso:
                continue

            cleaning_counts[d] = cleaning_counts.get(d, 0) + 1
            workload_score[d] = workload_score.get(d, 0) + int(row.get("load_score") or 0)

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
            "debug": {"all_task_rows": len(task_rows), "counted_days": cleaning_counts},
        }
    except Exception as e:
        logger.error(f"compat get_shift_board failed: year={year} month={month} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="shift board fetch failed")
