from datetime import date, datetime, timedelta, timezone
import os
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from supabase import create_client

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


def _get_order_supabase():
    order_url = os.getenv("ORDER_SUPABASE_URL")
    order_key = os.getenv("ORDER_SUPABASE_SERVICE_KEY")
    if not order_url or not order_key:
        raise RuntimeError("ORDER_SUPABASE_URL / ORDER_SUPABASE_SERVICE_KEY が未設定です。")
    return create_client(order_url, order_key)


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


def _date_key(value) -> str:
    return str(value or "")[:10]


def _int_value(value, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def _room_lookup_key(property_name: str, room_name: str) -> str:
    return f"{property_name}::{room_name}"


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


@router.get("/order-due-schedules")
def get_order_due_schedules(
    year: int,
    month: int,
    current_user: dict = Depends(require_admin_or_leader),
):
    month_start = date(year, month, 1)
    if month == 12:
        month_end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        month_end = date(year, month + 1, 1) - timedelta(days=1)

    try:
        order_supabase = _get_order_supabase()
        res = (
            order_supabase
            .table("orders")
            .select("id,order_no,status,item_name,quantity,unit,usage_place,delivery_place,supplier,due_date")
            .gte("due_date", month_start.isoformat())
            .lte("due_date", month_end.isoformat())
            .neq("status", "キャンセル")
            .order("due_date")
            .execute()
        )
    except Exception as e:
        logger.error(f"get_order_due_schedules failed: year={year} month={month} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="発注納期の取得に失敗しました。")

    items = []
    for row in res.data or []:
        due_date = _date_key(row.get("due_date"))
        if not due_date:
            continue
        items.append({
            "id": str(row.get("id") or ""),
            "order_no": row.get("order_no") or "",
            "status": row.get("status") or "",
            "item_name": row.get("item_name") or "",
            "quantity": row.get("quantity"),
            "unit": row.get("unit"),
            "usage_place": row.get("usage_place"),
            "delivery_place": row.get("delivery_place"),
            "supplier": row.get("supplier"),
            "due_date": due_date,
        })

    logger.info(f"get_order_due_schedules: year={year} month={month} count={len(items)}")
    return {"items": items}


@router.get("/prep-list")
def get_prep_list(current_user: dict = Depends(require_admin_or_leader)):
    """
    物件管理 > 準備物確認 用。
    明日以降の清掃タスクに対して、部屋マスタの準備数を付与して返す。
    """
    today = _today_jst_iso()
    end_date = (date.fromisoformat(today) + timedelta(days=14)).isoformat()
    excluded_statuses = {"CXL", "キャンセル", "cancelled", "Cancelled", "完了"}

    try:
        task_res = (
            supabase
            .table("cleaning_tasks")
            .select("*")
            .gt("task_date", today)
            .lte("task_date", end_date)
            .order("task_date")
            .order("property_name")
            .order("room_name")
            .execute()
        )
        prop_res = supabase.table("properties").select("id, property_name").execute()
        room_res = (
            supabase
            .table("rooms")
            .select("id, property_id, room_name, room_key, normalized_room_key, prep_d, prep_s, prep_spare_s, prep_ta")
            .eq("is_active", True)
            .execute()
        )
    except Exception as e:
        logger.error(f"get_prep_list failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="準備物一覧の取得に失敗しました。")

    property_name_by_id = {
        str(row.get("id")): str(row.get("property_name") or "")
        for row in (prop_res.data or [])
        if row.get("id")
    }

    room_by_pair: dict[str, dict] = {}
    room_by_key: dict[str, dict] = {}
    for room in room_res.data or []:
        property_name = property_name_by_id.get(str(room.get("property_id") or ""), "")
        room_name = str(room.get("room_name") or "")
        if property_name and room_name:
            room_by_pair[_room_lookup_key(property_name, room_name)] = room
        for key in [room.get("room_key"), room.get("normalized_room_key")]:
            if key:
                room_by_key[str(key)] = room

    items = []
    for task in task_res.data or []:
        status = str(task.get("status") or "")
        if status in excluded_statuses:
            continue

        property_name = str(task.get("property_name") or "")
        room_name = str(task.get("room_name") or "")
        room_key = str(task.get("room_key") or "")
        room = room_by_key.get(room_key) or room_by_pair.get(_room_lookup_key(property_name, room_name)) or {}

        guest_count = (
            task.get("next_guest_count")
            or task.get("guest_count")
            or task.get("adult_count")
            or task.get("guests")
        )
        stay_nights = (
            task.get("next_stay_nights")
            or task.get("stay_nights")
            or task.get("nights")
            or task.get("gap_nights")
        )

        items.append({
            "task_id": task.get("id"),
            "task_date": _date_key(task.get("task_date") or task.get("checkout_date")),
            "property_name": property_name,
            "room_name": room_name,
            "room_key": room_key,
            "towel_count": _calc_towel_count(property_name, guest_count, stay_nights),
            "prep_d": _int_value(room.get("prep_d"), 0),
            "prep_s": _int_value(room.get("prep_s"), 0),
            "prep_spare_s": _int_value(room.get("prep_spare_s"), 0),
            "prep_ta": _int_value(room.get("prep_ta"), 0),
            "note": task.get("note") or "",
        })

    logger.info(f"get_prep_list: today={today} end={end_date} count={len(items)}")
    return {"items": items, "start_date": today, "end_date": end_date}


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
