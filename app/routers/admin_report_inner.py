from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.db import supabase
from app.logger import get_logger
from app.services.auth_service import require_admin_or_leader

router = APIRouter()
logger = get_logger(__name__)


class WorklogUpdateBody(BaseModel):
    worklog_id: str
    work_date: str
    property_name: str = ""
    room_name: str = ""
    work_start_time: str = ""
    start_time: str
    end_time: str
    break_minutes: int = 0
    work_type: str = "cleaning"
    note: str = ""


class WorklogDeleteBody(BaseModel):
    worklog_id: str


def _minutes_between(start_time: str, end_time: str, break_minutes: int) -> int:
    try:
        sh, sm = [int(x) for x in start_time.split(":")[:2]]
        eh, em = [int(x) for x in end_time.split(":")[:2]]
    except Exception:
        return 0
    return max((eh * 60 + em) - (sh * 60 + sm) - int(break_minutes or 0), 0)


def _staff_map_by_id(ids: list[str]) -> dict[str, dict]:
    clean_ids = [x for x in dict.fromkeys(ids) if x]
    if not clean_ids:
        return {}
    try:
        res = supabase.table("staff_members").select("id, staff_name, staff_code").in_("id", clean_ids).execute()
    except Exception as e:
        logger.warning(f"staff id lookup skipped: {e}")
        return {}
    return {str(row.get("id")): row for row in (res.data or [])}


def _fetch_worklog_rows(work_date: str) -> list[dict]:
    last_error = None
    for table_name in ["worklogs", "work_logs"]:
        try:
            res = (
                supabase.table(table_name)
                .select("*")
                .eq("work_date", work_date)
                .execute()
            )
            rows = res.data or []
            rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
            return rows
        except Exception as e:
            last_error = e
            logger.warning(f"worklog lookup skipped: table={table_name} error={e}")
            continue
    raise last_error or RuntimeError("worklog lookup failed")


def _find_worklog_table(worklog_id: str) -> str:
    last_error = None
    for table_name in ["worklogs", "work_logs"]:
        try:
            res = supabase.table(table_name).select("id").eq("id", worklog_id).limit(1).execute()
            if res.data:
                return table_name
        except Exception as e:
            last_error = e
            logger.warning(f"worklog table detection skipped: table={table_name} error={e}")
    if last_error:
        logger.warning(f"worklog table detection final error: {last_error}")
    raise HTTPException(status_code=404, detail="対象の実働報告が見つかりません。")


@router.get("/worklogs/today")
def get_admin_worklogs(date_param: str | None = Query(default=None, alias="date"), current_user: dict = Depends(require_admin_or_leader)):
    work_date = date_param or date.today().isoformat()
    try:
        rows = _fetch_worklog_rows(work_date)
    except Exception as e:
        logger.error(f"get_admin_worklogs failed: date={work_date} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="実働報告の取得に失敗しました。")

    staff_by_id = _staff_map_by_id([str(row.get("user_id") or row.get("staff_id") or "") for row in rows])
    worklogs = []
    for row in rows:
        sid = str(row.get("user_id") or row.get("staff_id") or "")
        staff = staff_by_id.get(sid, {})
        break_minutes = int(row.get("break_minutes") or 0)
        work_minutes = row.get("work_minutes")
        if work_minutes is None:
            work_minutes = _minutes_between(row.get("start_time") or "", row.get("end_time") or "", break_minutes)
        worklogs.append({
            "id": row.get("id"),
            "user_id": sid,
            "staff_name": row.get("staff_name") or staff.get("staff_name") or "",
            "staff_code": row.get("staff_code") or staff.get("staff_code") or "",
            "work_date": row.get("work_date") or work_date,
            "property_name": row.get("property_name") or "",
            "room_name": row.get("room_name") or "",
            "work_start_time": row.get("work_start_time") or row.get("start_time") or "",
            "start_time": row.get("start_time") or "",
            "end_time": row.get("end_time") or "",
            "break_minutes": break_minutes,
            "work_type": row.get("work_type") or "",
            "note": row.get("note") or "",
            "created_at": row.get("created_at") or "",
            "work_minutes": int(work_minutes or 0),
        })
    return {"worklogs": worklogs}


@router.post("/worklogs/update")
def update_admin_worklog(payload: WorklogUpdateBody, current_user: dict = Depends(require_admin_or_leader)):
    if not payload.worklog_id:
        raise HTTPException(status_code=400, detail="worklog_id is required")
    if not payload.work_date or not payload.start_time or not payload.end_time:
        raise HTTPException(status_code=400, detail="日付・出勤・退勤は必須です。")

    table_name = _find_worklog_table(payload.worklog_id)
    break_minutes = max(int(payload.break_minutes or 0), 0)
    update_data = {
        "work_date": payload.work_date,
        "property_name": payload.property_name.strip(),
        "room_name": payload.room_name.strip(),
        "work_start_time": payload.work_start_time or payload.start_time,
        "start_time": payload.start_time,
        "end_time": payload.end_time,
        "break_minutes": break_minutes,
        "work_type": payload.work_type or "cleaning",
        "note": payload.note,
        "work_minutes": _minutes_between(payload.start_time, payload.end_time, break_minutes),
    }

    try:
        res = supabase.table(table_name).update(update_data).eq("id", payload.worklog_id).execute()
    except Exception as e:
        logger.error(f"update_admin_worklog failed: id={payload.worklog_id} table={table_name} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="実働報告の更新に失敗しました。")

    if not res.data:
        raise HTTPException(status_code=404, detail="対象の実働報告が見つかりません。")
    logger.info(f"update_admin_worklog: id={payload.worklog_id} user={current_user.get('user_id')}")
    return {"ok": True, "data": res.data[0]}


@router.post("/worklogs/delete")
def delete_admin_worklog(payload: WorklogDeleteBody, current_user: dict = Depends(require_admin_or_leader)):
    if not payload.worklog_id:
        raise HTTPException(status_code=400, detail="worklog_id is required")

    table_name = _find_worklog_table(payload.worklog_id)
    try:
        res = supabase.table(table_name).delete().eq("id", payload.worklog_id).execute()
    except Exception as e:
        logger.error(f"delete_admin_worklog failed: id={payload.worklog_id} table={table_name} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="実働報告の削除に失敗しました。")

    if not res.data:
        raise HTTPException(status_code=404, detail="対象の実働報告が見つかりません。")
    logger.info(f"delete_admin_worklog: id={payload.worklog_id} user={current_user.get('user_id')}")
    return {"ok": True, "worklog_id": payload.worklog_id}


@router.get("/lost-items")
def get_admin_lost_items(current_user: dict = Depends(require_admin_or_leader)):
    try:
        res = supabase.table("lost_items").select("*").execute()
    except Exception as e:
        logger.error(f"get_admin_lost_items failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="忘れ物一覧の取得に失敗しました。")

    rows = res.data or []
    rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    items = []
    for row in rows:
        items.append({
            "id": row.get("id"),
            "task_id": row.get("task_id"),
            "task_date": row.get("task_date") or row.get("found_date") or "",
            "property_name": row.get("property_name") or "",
            "room_name": row.get("room_name") or "",
            "item_description": row.get("item_description") or row.get("item_name") or "",
            "photo_url": row.get("photo_url") or row.get("image_url") or "",
            "status": row.get("status") or "",
            "note": row.get("note") or "",
            "reported_by": row.get("reported_by") or row.get("created_by_staff_code") or "",
            "reported_by_name": row.get("reported_by_name") or row.get("created_by_staff_name") or "",
            "created_at": row.get("created_at") or "",
        })
    return {"items": items}
