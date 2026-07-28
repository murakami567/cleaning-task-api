from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.db import supabase
from app.logger import get_logger
from app.services.auth_service import require_admin_or_leader

router = APIRouter(tags=["staff-schedules"])
logger = get_logger(__name__)

TABLE_NAME = "staff_day_schedules"


def _clean_time(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


@router.get("/staff-schedules")
def get_staff_schedules(shift_date: str):
    """指定日のスタッフ予定を取得する。

    管理画面の既存フロントはGET時にAuthorizationヘッダーを送らないため、
    他の互換系参照APIと同様に読み取りは認証なしで許可する。
    """
    try:
        res = (
            supabase.table(TABLE_NAME)
            .select("id,shift_date,staff_id,start_time,end_time,place,work_category,details")
            .eq("shift_date", shift_date)
            .order("start_time")
            .execute()
        )
        rows = res.data or []
        logger.info(f"get_staff_schedules: shift_date={shift_date} count={len(rows)}")
        return rows
    except Exception as e:
        logger.error(
            f"get_staff_schedules failed: shift_date={shift_date} {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="スケジュールの取得に失敗しました。")


@router.post("/staff-schedules/upsert")
def upsert_staff_schedule(
    body: dict[str, Any] = Body(...),
    current_user: dict = Depends(require_admin_or_leader),
):
    schedule_id = str(body.get("id") or "").strip()
    shift_date = str(body.get("shift_date") or "").strip()[:10]
    staff_id = str(body.get("staff_id") or "").strip()
    start_time = _clean_time(body.get("start_time"))
    end_time = _clean_time(body.get("end_time"))

    if not shift_date:
        raise HTTPException(status_code=400, detail="shift_date は必須です。")
    if not staff_id:
        raise HTTPException(status_code=400, detail="staff_id は必須です。")
    if not start_time or not end_time:
        raise HTTPException(status_code=400, detail="開始・終了時刻は必須です。")
    if end_time <= start_time:
        raise HTTPException(status_code=400, detail="終了時刻は開始時刻より後にしてください。")

    payload = {
        "shift_date": shift_date,
        "staff_id": staff_id,
        "start_time": start_time,
        "end_time": end_time,
        "place": str(body.get("place") or "").strip(),
        "work_category": str(body.get("work_category") or "").strip(),
        "details": str(body.get("details") or "").strip(),
    }

    try:
        if schedule_id:
            res = (
                supabase.table(TABLE_NAME)
                .update(payload)
                .eq("id", schedule_id)
                .execute()
            )
        else:
            res = supabase.table(TABLE_NAME).insert(payload).execute()

        if not res.data:
            raise HTTPException(status_code=500, detail="スケジュールを保存できませんでした。")

        saved = res.data[0]
        logger.info(
            f"upsert_staff_schedule: id={saved.get('id')} shift_date={shift_date} staff_id={staff_id}"
        )
        return saved
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"upsert_staff_schedule failed: id={schedule_id} shift_date={shift_date} staff_id={staff_id} {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="スケジュールの保存に失敗しました。")


@router.post("/staff-schedules/delete")
def delete_staff_schedule(
    id: str = Body(..., embed=True),
    current_user: dict = Depends(require_admin_or_leader),
):
    schedule_id = str(id or "").strip()
    if not schedule_id:
        raise HTTPException(status_code=400, detail="id は必須です。")

    try:
        res = supabase.table(TABLE_NAME).delete().eq("id", schedule_id).execute()
        logger.info(f"delete_staff_schedule: id={schedule_id}")
        return {"ok": True, "data": res.data or []}
    except Exception as e:
        logger.error(f"delete_staff_schedule failed: id={schedule_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="スケジュールの削除に失敗しました。")