from datetime import date

from fastapi import APIRouter, Depends, HTTPException

from app.db import supabase
from app.logger import get_logger
from app.services.auth_service import require_admin_or_leader
from app.routers.admin_portal import _get_today_attendance_map, _enrich_shift_with_attendance
from app.routers.admin_report_inner import router as admin_report_inner_router

router = APIRouter(prefix="/api/admin-portal", tags=["admin-portal"])
router.include_router(admin_report_inner_router)
logger = get_logger(__name__)


def _filter_active_shift_entries(today_shift: dict | None) -> dict | None:
    """本日の社内スケジュールから、アカウント管理で無効化されたスタッフを除外する。"""
    if not today_shift:
        return today_shift

    active_entries = []
    for entry in today_shift.get("shift_entries") or []:
        staff = entry.get("staff_members") or {}
        if staff.get("is_active") is False:
            continue
        active_entries.append(entry)

    return {
        **today_shift,
        "shift_entries": active_entries,
    }


@router.get("/home")
def get_admin_home_active_only(current_user: dict = Depends(require_admin_or_leader)):
    today = date.today().isoformat()
    try:
        message_res = (
            supabase
            .table("portal_messages")
            .select("*")
            .eq("target_date", today)
            .order("updated_at", desc=True)
            .execute()
        )

        shift_res = (
            supabase
            .table("shift_days")
            .select("*, shift_entries(*, staff_members(*))")
            .eq("shift_date", today)
            .execute()
        )

        on_break_res = (
            supabase
            .table("staff_members")
            .select("id, staff_name, staff_code, role, break_started_at")
            .eq("on_break", True)
            .eq("is_active", True)
            .order("break_started_at")
            .execute()
        )
    except Exception as e:
        logger.error(f"get_admin_home_active_only failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="ホーム情報の取得に失敗しました。")

    today_shift = shift_res.data[0] if shift_res.data else None
    today_shift = _filter_active_shift_entries(today_shift)
    attendance_map = _get_today_attendance_map(today)
    today_shift = _enrich_shift_with_attendance(today_shift, attendance_map)

    logger.info(
        f"get_admin_home_active_only: today={today} attendance_count={len(attendance_map)} shift_count={len((today_shift or {}).get('shift_entries') or [])}"
    )
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
