from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.db import supabase
from app.logger import get_logger
from app.services.auth_service import require_admin_or_leader
from app.services.lineworks_service import (
    PRIVATE_KEY,
    BOT_ID,
    CLIENT_ID,
    SERVICE_ACCOUNT,
    send_text_to_channel,
)

router = APIRouter(prefix="/lineworks", tags=["lineworks"])
logger = get_logger(__name__)


@router.get("/debug-config")
def debug_config(current_user: dict = Depends(require_admin_or_leader)):
    """
    LINE WORKS 環境変数の設定状態を確認するための診断 (鍵そのものは漏らさない)。
    """
    key = PRIVATE_KEY or ""
    return {
        "bot_id_set": bool(BOT_ID),
        "client_id_set": bool(CLIENT_ID),
        "service_account_set": bool(SERVICE_ACCOUNT),
        "private_key_length": len(key),
        "private_key_newline_count": key.count("\n"),
        "private_key_has_begin": "-----BEGIN" in key,
        "private_key_has_end": "-----END" in key,
        "private_key_begin_marker": (
            key.split("\n")[0] if key else ""
        ),
        "private_key_end_marker": (
            [line for line in key.split("\n") if line.startswith("-----END")][0]
            if "-----END" in key
            else ""
        ),
    }


@router.get("/next-day-targets")
def get_next_day_targets(current_user: dict = Depends(require_admin_or_leader)):
    """
    翌日連絡の宛先候補を返す。
    対象: 翌日のシフトで status='出勤' かつ role='staff'。
    """
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    try:
        day_res = (
            supabase.table("shift_days")
            .select("*, shift_entries(*, staff_members(*))")
            .eq("shift_date", tomorrow)
            .limit(1)
            .execute()
        )
    except Exception as e:
        logger.error(f"next-day-targets shift_days fetch failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="シフト取得に失敗しました。")

    day = (day_res.data or [None])[0]
    items: list[dict[str, Any]] = []

    if day:
        for entry in day.get("shift_entries") or []:
            if entry.get("status") != "出勤":
                continue
            staff = entry.get("staff_members") or {}
            if (staff.get("role") or "") != "staff":
                continue
            if not staff.get("is_active", True):
                continue
            items.append({
                "staff_id": staff.get("id"),
                "staff_code": staff.get("staff_code") or "",
                "staff_name": staff.get("staff_name") or "",
                "sort_order": staff.get("sort_order") or 9999,
                "lineworks_channel_id": staff.get("lineworks_channel_id") or "",
                "assigned_area": entry.get("assigned_area") or "",
            })

    items.sort(key=lambda x: (x.get("sort_order") or 9999, x.get("staff_name") or ""))

    return {"target_date": tomorrow, "items": items}


@router.post("/next-day-notification")
def send_next_day_notification(
    items: list[dict[str, Any]] = Body(..., embed=True),
    current_user: dict = Depends(require_admin_or_leader),
):
    """
    翌日連絡を LINE WORKS に送信する。
    Body: { items: [{ staff_id, location, channel_id?, name? }, ...] }
    channel_id が無い場合は staff_members から引く。
    """
    if not items:
        raise HTTPException(status_code=400, detail="送信対象がありません。")

    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    # channel_id 未指定の staff_id は DB から補完
    need_lookup_ids = [str(it.get("staff_id")) for it in items if it.get("staff_id") and not it.get("channel_id")]
    channel_lookup: dict[str, str] = {}
    if need_lookup_ids:
        try:
            res = (
                supabase.table("staff_members")
                .select("id, lineworks_channel_id")
                .in_("id", need_lookup_ids)
                .execute()
            )
            for row in res.data or []:
                channel_lookup[str(row["id"])] = row.get("lineworks_channel_id") or ""
        except Exception as e:
            logger.error(f"channel_id lookup failed: {e}", exc_info=True)

    results: list[dict[str, Any]] = []
    sent_count = 0

    for it in items:
        staff_id = str(it.get("staff_id") or "")
        name = str(it.get("name") or "")
        location = str(it.get("location") or "").strip()
        channel_id = str(it.get("channel_id") or channel_lookup.get(staff_id) or "")

        if not channel_id:
            results.append({
                "staff_id": staff_id,
                "name": name,
                "ok": False,
                "error": "LINE WORKS チャンネルID未設定",
            })
            continue

        if not location:
            results.append({
                "staff_id": staff_id,
                "name": name,
                "ok": False,
                "error": "出勤場所が未入力",
            })
            continue

        text = (
            f"【翌日連絡 {tomorrow}】\n"
            f"{name + ' さん、' if name else ''}明日の出勤場所は以下の通りです。\n\n"
            f"・場所: {location}\n\n"
            "よろしくお願いいたします。"
        )

        try:
            send_text_to_channel(channel_id, text)
            results.append({"staff_id": staff_id, "name": name, "ok": True})
            sent_count += 1
        except Exception as e:
            logger.error(
                f"lineworks send failed: staff_id={staff_id} channel={channel_id} {e}",
                exc_info=True,
            )
            results.append({
                "staff_id": staff_id,
                "name": name,
                "ok": False,
                "error": str(e)[:200],
            })

    logger.info(
        f"next-day-notification: date={tomorrow} sent={sent_count} total={len(items)}"
    )
    return {
        "target_date": tomorrow,
        "sent": sent_count,
        "total": len(items),
        "results": results,
    }
