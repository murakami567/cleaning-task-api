from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.db import supabase
from app.logger import get_logger
from app.services.auth_service import require_admin_or_leader
from app.services.jinjer_service import fetch_attendances

router = APIRouter(prefix="/api/admin-portal", tags=["admin-portal-home-override"])
logger = get_logger(__name__)

OFF_STATUSES = {"休み", "休日", "定休", "欠勤", "off", "OFF", "休"}


def _today_jst_iso() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=9)).date().isoformat()


def _get_staff_code_to_id() -> dict[str, str]:
    try:
        res = (
            supabase.table("staff_members")
            .select("id, staff_code")
            .eq("is_active", True)
            .limit(10000)
            .execute()
        )
    except Exception as e:
        logger.warning(f"staff code lookup skipped for attendance fallback: {e}")
        return {}

    out: dict[str, str] = {}
    for row in res.data or []:
        code = str(row.get("staff_code") or "").strip()
        staff_id = str(row.get("id") or "").strip()
        if code and staff_id:
            out[code] = staff_id
    return out


def _pick_better_attendance(prev: dict | None, row: dict) -> dict:
    if not prev:
        return row
    prev_in = prev.get("clock_in_at") or prev.get("started_at")
    row_in = row.get("clock_in_at") or row.get("started_at")
    if not prev_in and row_in:
        return row
    return prev


def _get_attendance_map_from_db(target_date: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for date_column in ["work_date", "date", "target_date"]:
        try:
            res = (
                supabase.table("attendance_logs")
                .select("*")
                .eq(date_column, target_date)
                .execute()
            )
        except Exception as e:
            logger.warning(f"attendance_logs lookup skipped: column={date_column} error={e}")
            continue

        for row in res.data or []:
            staff_id = row.get("staff_id") or row.get("user_id")
            if not staff_id:
                continue
            key = str(staff_id)
            out[key] = _pick_better_attendance(out.get(key), row)
    return out


def _get_attendance_map_from_jinjer_live(target_date: str) -> dict[str, dict]:
    """
    attendance_logs が未同期・空の場合の救済。
    ホーム表示時にJinjerから当日分を直接取得して社内スケジュールに反映する。
    DB保存は行わないため、既存同期処理はそのまま温存する。
    """
    try:
        items = fetch_attendances(target_date, target_date)
    except Exception as e:
        logger.warning(f"live Jinjer attendance fallback skipped: date={target_date} error={e}")
        return {}

    code_to_id = _get_staff_code_to_id()
    out: dict[str, dict] = {}
    for item in items:
        employee_id = str(item.get("employee_id") or "").strip()
        staff_id = code_to_id.get(employee_id)
        if not staff_id:
            continue
        row = {
            "staff_id": staff_id,
            "work_date": item.get("work_date") or target_date,
            "clock_in_at": item.get("clock_in_at"),
            "clock_out_at": item.get("clock_out_at"),
            "source": "jinjer_live",
            "raw_data": item.get("raw_data"),
        }
        out[staff_id] = _pick_better_attendance(out.get(staff_id), row)

    logger.info(f"live Jinjer attendance fallback: date={target_date} fetched={len(items)} matched={len(out)}")
    return out


def _get_attendance_map(target_date: str) -> dict[str, dict]:
    db_map = _get_attendance_map_from_db(target_date)
    if db_map:
        return db_map
    return _get_attendance_map_from_jinjer_live(target_date)


def _is_active_shift_entry(entry: dict) -> bool:
    staff = entry.get("staff_members") or {}
    if staff.get("is_active") is False:
        return False
    status = str(entry.get("status") or "")
    return status not in OFF_STATUSES


def _enrich_shift_with_attendance(today_shift: dict | None, attendance_map: dict[str, dict]) -> dict | None:
    if not today_shift:
        return today_shift

    enriched_entries = []
    for entry in today_shift.get("shift_entries") or []:
        if not _is_active_shift_entry(entry):
            continue

        staff = entry.get("staff_members") or {}
        staff_id = str(entry.get("staff_id") or staff.get("id") or entry.get("user_id") or "")
        attendance = attendance_map.get(staff_id, {})
        clock_in_at = attendance.get("clock_in_at") or attendance.get("started_at")
        clock_out_at = attendance.get("clock_out_at") or attendance.get("ended_at")
        attendance_status = "clocked_in" if clock_in_at else "not_clocked_in"

        enriched_entries.append({
            **entry,
            "clock_in_at": clock_in_at,
            "clock_out_at": clock_out_at,
            "attendance_status": attendance_status,
            "attendance": {
                "status": attendance_status,
                "clock_in_at": clock_in_at,
                "clock_out_at": clock_out_at,
                "source": attendance.get("source") or "jinjer",
            },
        })

    return {**today_shift, "shift_entries": enriched_entries}


@router.get("/home")
def get_admin_home_override(current_user: dict = Depends(require_admin_or_leader)):
    today = _today_jst_iso()
    try:
        message_res = (
            supabase.table("portal_messages")
            .select("*")
            .eq("target_date", today)
            .order("updated_at", desc=True)
            .execute()
        )
        shift_res = (
            supabase.table("shift_days")
            .select("*, shift_entries(*, staff_members(*))")
            .eq("shift_date", today)
            .execute()
        )
        on_break_res = (
            supabase.table("staff_members")
            .select("id, staff_name, staff_code, role, break_started_at")
            .eq("on_break", True)
            .eq("is_active", True)
            .order("break_started_at")
            .execute()
        )
    except Exception as e:
        logger.error(f"get_admin_home_override failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="ホーム情報の取得に失敗しました。")

    attendance_map = _get_attendance_map(today)
    today_shift = shift_res.data[0] if shift_res.data else None
    today_shift = _enrich_shift_with_attendance(today_shift, attendance_map)

    logger.info(f"get_admin_home_override: jst_today={today} attendance_count={len(attendance_map)}")
    return {
        "todayDate": today,
        "todayMessages": message_res.data or [],
        "todayShift": today_shift,
        "onBreakStaff": [
            {
                "id": row.get("id"),
                "name": row.get("staff_name"),
                "staff_code": row.get("staff_code"),
                "role": row.get("role"),
                "break_started_at": row.get("break_started_at"),
            }
            for row in (on_break_res.data or [])
        ],
    }
