import os
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Header, HTTPException

from app.db import supabase
from app.logger import get_logger
from app.services.lineworks_service import send_text_to_channel

router = APIRouter(prefix="/notifications", tags=["notifications"])
logger = get_logger(__name__)

TOMORROW_SCHEDULE_MESSAGE = "明日のスケジュールが確定しました。\nアプリから確認をお願いします。"
OFF_STATUSES = {"休み", "休日", "定休", "欠勤", "off", "OFF", "休"}
TARGET_ROLES = {"staff", "checker", "leader", "sub_admin", "admin"}


def _today_jst():
    return (datetime.now(timezone.utc) + timedelta(hours=9)).date()


def _require_cron_key(x_cron_key: str | None) -> None:
    expected = os.getenv("CRON_SECRET") or os.getenv("AUTO_ASSIGN_CRON_KEY")
    if not expected:
        raise HTTPException(status_code=500, detail="CRON_SECRET or AUTO_ASSIGN_CRON_KEY is not configured")
    if not x_cron_key or x_cron_key != expected:
        raise HTTPException(status_code=401, detail="Invalid cron key")


def _is_target_entry(entry: dict[str, Any]) -> bool:
    status = str(entry.get("status") or "")
    if status in OFF_STATUSES:
        return False

    staff = entry.get("staff_members") or {}
    if staff.get("is_active") is False:
        return False

    role = str(staff.get("role") or "")
    if role and role not in TARGET_ROLES:
        return False

    return True


@router.post("/tomorrow-schedule")
def send_tomorrow_schedule_notification(
    x_cron_key: str | None = Header(default=None, alias="X-CRON-KEY"),
):
    """
    Render Cron用。
    翌日出勤対象者に固定文面を自動送信する。
    既存の翌日連絡APIは残し、このエンドポイントは自動送信用として分離する。
    """
    _require_cron_key(x_cron_key)

    target_date = (_today_jst() + timedelta(days=1)).isoformat()

    try:
        day_res = (
            supabase.table("shift_days")
            .select("*, shift_entries(*, staff_members(*))")
            .eq("shift_date", target_date)
            .limit(1)
            .execute()
        )
    except Exception as e:
        logger.error(f"tomorrow schedule notification shift fetch failed: date={target_date} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="翌日シフト取得に失敗しました。")

    day = (day_res.data or [None])[0]
    if not day:
        logger.info(f"tomorrow schedule notification: date={target_date} no shift day")
        return {
            "ok": True,
            "target_date": target_date,
            "sent": 0,
            "total": 0,
            "skipped": [],
            "results": [],
        }

    candidates: dict[str, dict[str, Any]] = {}
    skipped: list[dict[str, Any]] = []

    for entry in day.get("shift_entries") or []:
        if not _is_target_entry(entry):
            continue
        staff = entry.get("staff_members") or {}
        staff_id = str(staff.get("id") or "")
        if not staff_id:
            continue
        channel_id = str(staff.get("lineworks_channel_id") or "").strip()
        if not channel_id:
            skipped.append({
                "staff_id": staff_id,
                "staff_name": staff.get("staff_name") or "",
                "reason": "lineworks_channel_id_missing",
            })
            continue
        candidates[staff_id] = {
            "staff_id": staff_id,
            "staff_name": staff.get("staff_name") or "",
            "staff_code": staff.get("staff_code") or "",
            "channel_id": channel_id,
            "sort_order": staff.get("sort_order") or 9999,
        }

    targets = sorted(candidates.values(), key=lambda x: (x.get("sort_order") or 9999, x.get("staff_name") or ""))
    results: list[dict[str, Any]] = []
    sent = 0

    for target in targets:
        try:
            send_text_to_channel(target["channel_id"], TOMORROW_SCHEDULE_MESSAGE)
            results.append({
                "staff_id": target["staff_id"],
                "staff_name": target["staff_name"],
                "ok": True,
            })
            sent += 1
        except Exception as e:
            logger.error(
                f"tomorrow schedule notification send failed: staff_id={target['staff_id']} channel={target['channel_id']} {e}",
                exc_info=True,
            )
            results.append({
                "staff_id": target["staff_id"],
                "staff_name": target["staff_name"],
                "ok": False,
                "error": str(e)[:200],
            })

    logger.info(
        f"tomorrow schedule notification: date={target_date} sent={sent} targets={len(targets)} skipped={len(skipped)}"
    )

    return {
        "ok": True,
        "target_date": target_date,
        "message": TOMORROW_SCHEDULE_MESSAGE,
        "sent": sent,
        "total": len(targets),
        "skipped": skipped,
        "results": results,
    }
