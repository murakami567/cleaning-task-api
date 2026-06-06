from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import supabase
from app.logger import get_logger
from app.services.auth_service import require_admin_or_leader


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


@router.get("/home")
def get_admin_home(current_user: dict = Depends(require_admin_or_leader)):
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
        logger.error(f"get_admin_home failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="ホーム情報の取得に失敗しました。")

    logger.info(f"get_admin_home: today={today}")
    return {
        "todayDate": today,
        "todayMessages": message_res.data or [],
        "todayShift": shift_res.data[0] if shift_res.data else None,
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


@router.get("/worklogs/today")
def get_today_worklogs(
    date: str | None = None,
    current_user: dict = Depends(require_admin_or_leader),
):
    from datetime import datetime

    target_date = date or datetime.today().strftime("%Y-%m-%d")

    try:
        worklog_res = (
            supabase
            .table("worklogs")
            .select("*")
            .eq("work_date", target_date)
            .order("start_time")
            .execute()
        )

        worklogs = worklog_res.data or []

        user_ids = list({
            row.get("user_id")
            for row in worklogs
            if row.get("user_id")
        })

        staff_map = {}

        if user_ids:
            staff_res = (
                supabase
                .table("staff_members")
                .select("id, staff_name, staff_code")
                .in_("id", user_ids)
                .execute()
            )

            for row in staff_res.data or []:
                staff_map[row.get("id")] = {
                    "staff_name": row.get("staff_name") or "",
                    "staff_code": row.get("staff_code") or "",
                }
    except Exception as e:
        logger.error(f"get_today_worklogs failed: date={target_date} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="実働ログの取得に失敗しました。")

    def calc_work_minutes(work_start_time: str | None, end_time: str | None, break_minutes: int | None):
        if not work_start_time or not end_time:
            return 0
        try:
            start_dt = datetime.strptime(work_start_time, "%H:%M")
            end_dt = datetime.strptime(end_time, "%H:%M")
            minutes = int((end_dt - start_dt).total_seconds() / 60)
            minutes -= int(break_minutes or 0)
            return max(minutes, 0)
        except Exception:
            return 0

    result = []
    for row in worklogs:
        staff_info = staff_map.get(row.get("user_id"), {})
        result.append({
            "id": row.get("id"),
            "user_id": row.get("user_id"),
            "staff_name": staff_info.get("staff_name", ""),
            "staff_code": staff_info.get("staff_code", ""),
            "work_date": row.get("work_date") or "",
            "property_name": row.get("property_name") or "",
            "room_name": row.get("room_name") or "",
            "work_start_time": row.get("work_start_time") or "",
            "start_time": row.get("start_time") or "",
            "end_time": row.get("end_time") or "",
            "break_minutes": row.get("break_minutes") or 0,
            "work_type": row.get("work_type") or "",
            "note": row.get("note") or "",
            "created_at": row.get("created_at") or "",
            "work_minutes": calc_work_minutes(
                row.get("work_start_time"),
                row.get("end_time"),
                row.get("break_minutes"),
            ),
        })

    logger.info(f"get_today_worklogs: date={target_date} count={len(result)}")
    return {"date": target_date, "worklogs": result}


@router.get("/prep-list")
def get_prep_list(current_user: dict = Depends(require_admin_or_leader)):
    """
    翌日以降の清掃タスクを部屋マスタの準備物 (D / S / 予備S / タ) と結合して返す。
    タオル数は cleaning_tasks の next_guest_count / next_stay_nights から算出。
    備考は cleaning_tasks.note。
    """
    today = date.today().isoformat()

    try:
        tasks_res = (
            supabase.table("cleaning_tasks")
            .select("*")
            .gt("task_date", today)
            .order("task_date")
            .order("property_name")
            .order("room_name")
            .execute()
        )

        rooms_res = (
            supabase.table("rooms")
            .select("room_key, prep_d, prep_s, prep_spare_s, prep_ta")
            .execute()
        )
    except Exception as e:
        logger.error(f"get_prep_list failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="準備物一覧の取得に失敗しました。")

    room_lookup = {}
    for row in rooms_res.data or []:
        key = row.get("room_key")
        if key:
            room_lookup[key] = row

    items = []
    for t in tasks_res.data or []:
        room_key = t.get("room_key")
        room = room_lookup.get(room_key, {})

        items.append({
            "task_id": t.get("id"),
            "task_date": t.get("task_date") or "",
            "property_name": t.get("property_name") or "",
            "room_name": t.get("room_name") or "",
            "room_key": room_key or "",
            "towel_count": _calc_towel_count(
                t.get("property_name") or "",
                t.get("next_guest_count"),
                t.get("next_stay_nights"),
            ),
            "prep_d": int(room.get("prep_d") or 0),
            "prep_s": int(room.get("prep_s") or 0),
            "prep_spare_s": int(room.get("prep_spare_s") or 0),
            "prep_ta": int(room.get("prep_ta") or 0),
            "note": t.get("note") or "",
        })

    logger.info(f"get_prep_list: count={len(items)}")
    return {"items": items}


@router.get("/lost-items")
def get_lost_items(current_user: dict = Depends(require_admin_or_leader)):
    """
    スタッフから報告された忘れ物の一覧。新しい順に返す。
    既存テーブルの found_date / item_name / image_url を
    フロント互換のキー (task_date / item_description / photo_url) に
    マッピングして返す。
    """
    try:
        res = (
            supabase.table("lost_items")
            .select("*")
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as e:
        logger.error(f"get_lost_items failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="忘れ物一覧の取得に失敗しました。")

    items = []
    for row in res.data or []:
        items.append({
            "id": row.get("id"),
            "task_id": None,
            "task_date": row.get("found_date") or "",
            "property_name": row.get("property_name") or "",
            "room_name": row.get("room_name") or "",
            "item_description": row.get("item_name") or "",
            "photo_url": row.get("image_url") or "",
            "status": row.get("status") or "",
            "note": row.get("note") or "",
            "reported_by": row.get("created_by_staff_code") or "",
            "reported_by_name": row.get("created_by_staff_name") or "",
            "created_at": row.get("created_at") or "",
        })

    logger.info(f"get_lost_items: count={len(items)}")
    return {"items": items}
