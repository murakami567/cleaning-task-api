from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query

from app.db import supabase
from app.logger import get_logger
from app.services.auth_service import require_admin_or_leader

router = APIRouter()
logger = get_logger(__name__)


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
