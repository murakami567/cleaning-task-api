from datetime import date, datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import supabase
from app.logger import get_logger
from app.services.auth_service import require_admin_or_leader


def _today_jst_iso() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=9)).date().isoformat()


def _calc_towel_count(property_name: str, next_guest_count, next_stay_nights):
    """employee.calc_towel_display と同じロジックを管理画面側でも使う。"""
    if property_name in ["FFFホテル", "やなぎ橋"]:
        return ""
    try:
        guests = int(next_guest_count or 0)
        nights = int(next_stay_nights or 0)
    except Exception:
        return "-"
    if guests <= 0 or nights <= 0:
        return "-"
    if nights >= 8:
        return guests * 3
    if nights >= 3:
        return guests * 2
    return guests


router = APIRouter(prefix="/api/admin-portal", tags=["admin-portal"])
logger = get_logger(__name__)


class TodayMessageBody(BaseModel):
    target_date: str
    message: str


class PortalScheduleBody(BaseModel):
    start_date: str
    end_date: str
    assignee_ids: list[str] = []
    assignee_names: list[str] = []
    title: str
    description: str = ""


def _get_today_attendance_map(today: str) -> dict[str, dict]:
    """
    Jinjer同期後の打刻情報を staff_id ごとに返す。
    attendance_logs テーブルが未作成・未同期でもホーム画面は壊さない。
    想定カラム:
      - work_date/date/target_date のいずれか
      - staff_id
      - staff_name
      - clock_in_at
      - clock_out_at
      - source
    """
    date_columns = ["work_date", "date", "target_date"]

    for date_column in date_columns:
        try:
            res = (
                supabase
                .table("attendance_logs")
                .select("*")
                .eq(date_column, today)
                .execute()
            )
        except Exception as e:
            logger.warning(
                f"attendance_logs lookup skipped: column={date_column} error={e}"
            )
            continue

        attendance_map: dict[str, dict] = {}
        for row in res.data or []:
            staff_id = row.get("staff_id") or row.get("user_id")
            if not staff_id:
                continue
            attendance_map[staff_id] = row

        return attendance_map

    return {}


def _is_active_shift_entry(entry: dict) -> bool:
    staff = entry.get("staff_members") or {}
    if staff.get("is_active") is False:
        return False
    status = str(entry.get("status") or "")
    if status in ["休み", "定休", "欠勤", "off", "OFF", "休"]:
        return False
    return True


def _enrich_shift_with_attendance(today_shift: dict | None, attendance_map: dict[str, dict]) -> dict | None:
    if not today_shift:
        return today_shift

    entries = today_shift.get("shift_entries") or []
    enriched_entries = []

    for entry in entries:
        if not _is_active_shift_entry(entry):
            continue

        staff = entry.get("staff_members") or {}
        staff_id = entry.get("staff_id") or staff.get("id") or entry.get("user_id")
        attendance = attendance_map.get(staff_id or "", {})

        clock_in_at = attendance.get("clock_in_at") or attendance.get("started_at")
        clock_out_at = attendance.get("clock_out_at") or attendance.get("ended_at")
        attendance_status = "clocked_in" if clock_in_at else "not_clocked_in"

        enriched = {
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
        }
        enriched_entries.append(enriched)

    return {
        **today_shift,
        "shift_entries": enriched_entries,
    }


@router.get("/home")
def get_admin_home(current_user: dict = Depends(require_admin_or_leader)):
    today = _today_jst_iso()
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
        logger.error(f"get_admin_home failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="ホーム情報の取得に失敗しました。")

    today_shift = shift_res.data[0] if shift_res.data else None
    attendance_map = _get_today_attendance_map(today)
    today_shift = _enrich_shift_with_attendance(today_shift, attendance_map)

    logger.info(
        f"get_admin_home: jst_today={today} attendance_count={len(attendance_map)}"
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


@router.post("/today-message")
def save_today_message(
    payload: TodayMessageBody,
    current_user: dict = Depends(require_admin_or_leader),
):
    user_id = current_user["user_id"]
    target_date = payload.target_date
    message = payload.message.strip()

    if not target_date:
        raise HTTPException(status_code=400, detail="target_date は必須です。")

    if not message:
        raise HTTPException(status_code=400, detail="message は必須です。")

    try:
        res = (
            supabase
            .table("portal_messages")
            .insert({
                "target_date": target_date,
                "message": message,
                "updated_by": user_id,
            })
            .execute()
        )
    except Exception as e:
        logger.error(f"save_today_message failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="メッセージの保存に失敗しました。")

    logger.info(f"save_today_message: user_id={user_id} date={target_date}")
    return {"ok": True, "data": res.data}


@router.get("/calendar")
def get_admin_calendar(
    year: int,
    month: int,
    current_user: dict = Depends(require_admin_or_leader),
):
    start_date = date(year, month, 1).isoformat()
    if month == 12:
        end_date = date(year + 1, 1, 1).isoformat()
    else:
        end_date = date(year, month + 1, 1).isoformat()

    try:
        res = (
            supabase
            .table("shift_days")
            .select("*, shift_entries(*, staff_members(*))")
            .gte("shift_date", start_date)
            .lt("shift_date", end_date)
            .order("shift_date")
            .execute()
        )
    except Exception as e:
        logger.error(f"get_admin_calendar failed: year={year} month={month} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="カレンダー情報の取得に失敗しました。")

    logger.info(f"get_admin_calendar: year={year} month={month} days={len(res.data or [])}")
    return {"days": res.data or []}


@router.get("/calendar-schedules")
def get_calendar_schedules(
    year: int,
    month: int,
    current_user: dict = Depends(require_admin_or_leader),
):
    month_start = date(year, month, 1)
    if month == 12:
        month_end = date(year + 1, 1, 1)
    else:
        month_end = date(year, month + 1, 1)

    try:
        res = (
            supabase
            .table("portal_schedules")
            .select("*")
            .lte("start_date", month_end.isoformat())
            .gte("end_date", month_start.isoformat())
            .order("start_date")
            .execute()
        )
    except Exception as e:
        logger.error(f"get_calendar_schedules failed: year={year} month={month} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="スケジュールの取得に失敗しました。")

    logger.info(f"get_calendar_schedules: year={year} month={month} count={len(res.data or [])}")
    return {"schedules": res.data or []}


@router.post("/calendar-schedules")
def create_calendar_schedule(
    payload: PortalScheduleBody,
    current_user: dict = Depends(require_admin_or_leader),
):
    user_id = current_user["user_id"]

    if not payload.start_date:
        raise HTTPException(status_code=400, detail="start_date は必須です。")
    if not payload.end_date:
        raise HTTPException(status_code=400, detail="end_date は必須です。")
    if payload.end_date < payload.start_date:
        raise HTTPException(status_code=400, detail="end_date は start_date 以降にしてください。")
    if not payload.title.strip():
        raise HTTPException(status_code=400, detail="title は必須です。")

    try:
        res = (
            supabase
            .table("portal_schedules")
            .insert({
                "start_date": payload.start_date,
                "end_date": payload.end_date,
                "assignee_ids": payload.assignee_ids or [],
                "assignee_names": payload.assignee_names or [],
                "title": payload.title.strip(),
                "description": payload.description.strip(),
                "created_by": user_id,
            })
            .execute()
        )
    except Exception as e:
        logger.error(f"create_calendar_schedule failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="スケジュールの作成に失敗しました。")

    logger.info(f"create_calendar_schedule: user_id={user_id} title={payload.title}")
    return {"ok": True, "data": res.data}


@router.put("/calendar-schedules/{schedule_id}")
def update_calendar_schedule(
    schedule_id: str,
    payload: PortalScheduleBody,
    current_user: dict = Depends(require_admin_or_leader),
):
    if not payload.start_date:
        raise HTTPException(status_code=400, detail="start_date は必須です。")
    if not payload.end_date:
        raise HTTPException(status_code=400, detail="end_date は必須です。")
    if payload.end_date < payload.start_date:
        raise HTTPException(status_code=400, detail="end_date は start_date 以降にしてください。")
    if not payload.title.strip():
        raise HTTPException(status_code=400, detail="title は必須です。")

    try:
        res = (
            supabase
            .table("portal_schedules")
            .update({
                "start_date": payload.start_date,
                "end_date": payload.end_date,
                "assignee_ids": payload.assignee_ids or [],
                "assignee_names": payload.assignee_names or [],
                "title": payload.title.strip(),
                "description": payload.description.strip(),
                "updated_at": "now()",
            })
            .eq("id", schedule_id)
            .execute()
        )
    except Exception as e:
        logger.error(f"update_calendar_schedule failed: id={schedule_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="スケジュールの更新に失敗しました。")

    logger.info(f"update_calendar_schedule: id={schedule_id}")
    return {"ok": True, "data": res.data}


@router.delete("/calendar-schedules/{schedule_id}")
def delete_calendar_schedule(
    schedule_id: str,
    current_user: dict = Depends(require_admin_or_leader),
):
    try:
        res = (
            supabase
            .table("portal_schedules")
            .delete()
            .eq("id", schedule_id)
            .execute()
        )
    except Exception as e:
        logger.error(f"delete_calendar_schedule failed: id={schedule_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="スケジュールの削除に失敗しました。")

    logger.info(f"delete_calendar_schedule: id={schedule_id}")
    return {"ok": True, "data": res.data}
