from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.db import supabase
from app.logger import get_logger
from app.services.auth_service import require_admin_or_leader

router = APIRouter(prefix="/api/admin-portal", tags=["admin-portal-home-override"])
logger = get_logger(__name__)

OFF_STATUSES = {"休み", "休日", "定休", "欠勤", "off", "OFF", "休"}


def _today_jst_iso() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=9)).date().isoformat()


def _get_attendance_map(target_date: str) -> dict[str, dict]:
    """
    attendance_logs を staff_id ごとに返す。
    既存環境で日付カラムが work_date/date/target_date のどれでも拾えるようにする。
    以前は work_date クエリが0件でもそこで終了していたため、別カラム運用時に未打刻扱いになっていた。
    """
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
            prev = out.get(key)
            if not prev:
                out[key] = row
                continue
            # 複数打刻グループ等で同一スタッフが複数行ある場合は、出勤打刻がある行を優先
            if not (prev.get("clock_in_at") or prev.get("started_at")) and (row.get("clock_in_at") or row.get("started_at")):
                out[key] = row
    return out


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
