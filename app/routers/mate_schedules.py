from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.db import supabase
from app.logger import get_logger
from app.services.auth_service import require_admin_or_leader

router = APIRouter(tags=["mate-schedules"])
logger = get_logger(__name__)
TABLE_NAME = "mate_daily_schedules"


def _clean_locations(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


@router.get("/mate-schedules")
def get_mate_schedules(shift_date: str):
    try:
        res = (
            supabase.table(TABLE_NAME)
            .select("id,shift_date,staff_id,locations,note,created_at,updated_at")
            .eq("shift_date", str(shift_date or "")[:10])
            .execute()
        )
        return res.data or []
    except Exception as e:
        logger.error(f"get_mate_schedules failed: shift_date={shift_date} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="メイト勤務予定の取得に失敗しました。")


@router.post("/mate-schedules/upsert")
def upsert_mate_schedule(
    body: dict[str, Any] = Body(...),
    current_user: dict = Depends(require_admin_or_leader),
):
    shift_date = str(body.get("shift_date") or "").strip()[:10]
    staff_id = str(body.get("staff_id") or "").strip()
    locations = _clean_locations(body.get("locations"))
    note = str(body.get("note") or "").strip()

    if not shift_date:
        raise HTTPException(status_code=400, detail="shift_date は必須です。")
    if not staff_id:
        raise HTTPException(status_code=400, detail="staff_id は必須です。")

    payload = {
        "shift_date": shift_date,
        "staff_id": staff_id,
        "locations": locations,
        "note": note,
    }

    try:
        existing = (
            supabase.table(TABLE_NAME)
            .select("id")
            .eq("shift_date", shift_date)
            .eq("staff_id", staff_id)
            .limit(1)
            .execute()
        )
        if existing.data:
            schedule_id = existing.data[0]["id"]
            res = supabase.table(TABLE_NAME).update(payload).eq("id", schedule_id).execute()
        else:
            res = supabase.table(TABLE_NAME).insert(payload).execute()
        if not res.data:
            raise HTTPException(status_code=500, detail="メイト勤務予定を保存できませんでした。")
        return res.data[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"upsert_mate_schedule failed: shift_date={shift_date} staff_id={staff_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="メイト勤務予定の保存に失敗しました。")


@router.post("/mate-schedules/delete")
def delete_mate_schedule(
    body: dict[str, Any] = Body(...),
    current_user: dict = Depends(require_admin_or_leader),
):
    schedule_id = str(body.get("id") or "").strip()
    if not schedule_id:
        raise HTTPException(status_code=400, detail="id は必須です。")
    try:
        res = supabase.table(TABLE_NAME).delete().eq("id", schedule_id).execute()
        return {"ok": True, "data": res.data or []}
    except Exception as e:
        logger.error(f"delete_mate_schedule failed: id={schedule_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="メイト勤務予定の削除に失敗しました。")
