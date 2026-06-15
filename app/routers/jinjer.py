import os
import re
from datetime import date
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException

from app.db import supabase
from app.logger import get_logger
from app.services.auth_service import require_admin_or_leader
from app.services.jinjer_service import fetch_attendances, fetch_work_schedules

router = APIRouter(prefix="/jinjer", tags=["jinjer"])
logger = get_logger(__name__)


MONTH_PATTERN = re.compile(r"^\d{4}-\d{2}$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _normalize_time(t: str | None) -> str | None:
    """Jinjer は HH:MM:SS で返してくるので HH:MM に丸める。"""
    if not t:
        return None
    s = str(t).strip()
    if not s:
        return None
    return s[:5]


def _normalize_datetime(value: Any, work_date: str) -> str | None:
    """
    Jinjerの打刻時刻は日本時間として返る。
    Supabaseのtimestamptzにそのまま保存するとUTC扱いになり9時間ずれるため、
    タイムゾーンが無い値には +09:00 を明示する。
    """
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    if s.endswith("Z") or re.search(r"[+-]\d{2}:?\d{2}$", s):
        return s
    if re.match(r"^\d{4}-\d{2}-\d{2}[ T]\d{1,2}:\d{2}", s):
        normalized = s.replace(" ", "T")
        if re.match(r"^\d{4}-\d{2}-\d{2}T\d{1,2}:\d{2}$", normalized):
            normalized = f"{normalized}:00"
        return f"{normalized}+09:00"
    if re.match(r"^\d{1,2}:\d{2}", s):
        return f"{work_date}T{s[:5]}:00+09:00"
    return s


def _get_staff_maps() -> tuple[dict[str, dict], dict[str, dict]]:
    try:
        staff_res = (
            supabase.table("staff_members")
            .select("id, staff_name, staff_code")
            .eq("is_active", True)
            .limit(10000)
            .execute()
        )
    except Exception as e:
        logger.error(f"jinjer staff lookup failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="スタッフ取得に失敗しました。")

    code_to_staff: dict[str, dict] = {}
    id_to_staff: dict[str, dict] = {}
    for row in staff_res.data or []:
        staff_id = row.get("id")
        code = row.get("staff_code")
        if staff_id:
            id_to_staff[str(staff_id)] = row
        if code:
            code_to_staff[str(code).strip()] = row
    return code_to_staff, id_to_staff


def _sync_attendances_for_range(start: str, end: str) -> dict:
    if not DATE_PATTERN.match(start or ""):
        raise HTTPException(status_code=400, detail="start_date/target_date は YYYY-MM-DD 形式で指定してください。")
    if not DATE_PATTERN.match(end or ""):
        raise HTTPException(status_code=400, detail="end_date は YYYY-MM-DD 形式で指定してください。")
    if end < start:
        raise HTTPException(status_code=400, detail="end_date は start_date 以降にしてください。")

    items = fetch_attendances(start, end)
    code_to_staff, _ = _get_staff_maps()

    saved = 0
    skipped_no_staff: list[str] = []
    errors: list[dict[str, Any]] = []
    seen_no_staff: set[str] = set()

    for it in items:
        employee_id = it.get("employee_id")
        staff = code_to_staff.get(str(employee_id or "").strip())
        if not staff:
            if employee_id and employee_id not in seen_no_staff:
                seen_no_staff.add(employee_id)
                skipped_no_staff.append(employee_id)
            continue

        work_date = it["work_date"]
        payload = {
            "work_date": work_date,
            "staff_id": staff.get("id"),
            "staff_name": staff.get("staff_name") or "",
            "staff_code": staff.get("staff_code") or employee_id,
            "clock_in_at": _normalize_datetime(it.get("clock_in_at"), work_date),
            "clock_out_at": _normalize_datetime(it.get("clock_out_at"), work_date),
            "source": "jinjer",
            "raw_data": it.get("raw_data") or it,
        }

        try:
            existing = (
                supabase.table("attendance_logs")
                .select("id")
                .eq("work_date", work_date)
                .eq("staff_id", staff.get("id"))
                .eq("source", "jinjer")
                .limit(1)
                .execute()
            )
            if existing.data:
                supabase.table("attendance_logs").update(payload).eq("id", existing.data[0]["id"]).execute()
            else:
                supabase.table("attendance_logs").insert(payload).execute()
            saved += 1
        except Exception as e:
            errors.append({"employee_id": employee_id, "date": work_date, "error": str(e)[:200]})
            if len(errors) > 50:
                logger.error("sync_jinjer_attendances too many errors")
                break

    logger.info(
        f"sync_jinjer_attendances: start={start} end={end} fetched={len(items)} saved={saved} skipped_no_staff={len(skipped_no_staff)} errors={len(errors)}"
    )

    return {
        "start_date": start,
        "end_date": end,
        "fetched": len(items),
        "saved": saved,
        "skipped_no_staff": skipped_no_staff[:50],
        "errors": errors[:50],
    }


def _verify_cron_key(x_cron_key: str | None) -> None:
    expected = os.getenv("CRON_SECRET", "")
    if not expected:
        logger.error("CRON_SECRET is not set")
        raise HTTPException(status_code=500, detail="CRON_SECRET が設定されていません。")
    if not x_cron_key or x_cron_key != expected:
        raise HTTPException(status_code=401, detail="Invalid cron key")


@router.post("/shifts/sync")
def sync_jinjer_shifts(
    month: str = Body(..., embed=True),
    current_user: dict = Depends(require_admin_or_leader),
):
    """
    Jinjer から指定月のシフトを取得して shift_entries に反映する。
    """
    if not MONTH_PATTERN.match(month or ""):
        raise HTTPException(status_code=400, detail="month は YYYY-MM 形式で指定してください。")

    items = fetch_work_schedules(month)

    if not items:
        return {
            "month": month,
            "fetched": 0,
            "matched_staff": 0,
            "saved": 0,
            "skipped_no_staff": [],
            "errors": [],
        }

    code_to_staff, _ = _get_staff_maps()
    code_to_id = {code: staff.get("id") for code, staff in code_to_staff.items() if staff.get("id")}

    needed_dates: set[str] = set()
    for it in items:
        if it["employee_id"] in code_to_id and it.get("date"):
            needed_dates.add(it["date"])

    try:
        day_res = (
            supabase.table("shift_days")
            .select("id, shift_date")
            .in_("shift_date", list(needed_dates))
            .execute()
        ) if needed_dates else None
    except Exception as e:
        logger.error(f"sync_jinjer_shifts shift_days lookup failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="shift_days 取得に失敗しました。")

    date_to_day_id: dict[str, str] = {}
    for row in (day_res.data if day_res else []) or []:
        date_to_day_id[row["shift_date"]] = row["id"]

    for d in sorted(needed_dates):
        if d in date_to_day_id:
            continue
        try:
            created = (
                supabase.table("shift_days")
                .insert({"shift_date": d, "note": ""})
                .execute()
            )
            if created.data:
                date_to_day_id[d] = created.data[0]["id"]
        except Exception as e:
            logger.error(f"sync_jinjer_shifts shift_day create failed: date={d} {e}", exc_info=True)

    try:
        ent_res = (
            supabase.table("shift_entries")
            .select("id, shift_day_id, staff_id, assigned_area, note, status")
            .in_("shift_day_id", list(date_to_day_id.values()))
            .limit(50000)
            .execute()
        ) if date_to_day_id else None
    except Exception as e:
        logger.error(f"sync_jinjer_shifts entries lookup failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="shift_entries 取得に失敗しました。")

    existing: dict[tuple[str, str], dict[str, Any]] = {}
    for row in (ent_res.data if ent_res else []) or []:
        key = (row["shift_day_id"], row["staff_id"])
        existing[key] = row

    saved = 0
    skipped_no_staff: list[str] = []
    errors: list[dict[str, Any]] = []
    seen_no_staff: set[str] = set()

    for it in items:
        employee_id = it["employee_id"]
        staff_id = code_to_id.get(employee_id)
        if not staff_id:
            if employee_id not in seen_no_staff:
                seen_no_staff.add(employee_id)
                skipped_no_staff.append(employee_id)
            continue

        shift_day_id = date_to_day_id.get(it["date"])
        if not shift_day_id:
            continue

        start_time = _normalize_time(it.get("start"))
        end_time = _normalize_time(it.get("end"))

        prev = existing.get((shift_day_id, staff_id)) or {}
        prev_status = prev.get("status")
        new_status = prev_status if prev_status in ("欠勤", "遅刻") else "出勤"

        payload = {
            "shift_day_id": shift_day_id,
            "staff_id": staff_id,
            "status": new_status,
            "start_time": start_time,
            "end_time": end_time,
            "assigned_area": prev.get("assigned_area") or "",
            "note": prev.get("note") or "",
        }

        try:
            if prev.get("id"):
                supabase.table("shift_entries").update(payload).eq("id", prev["id"]).execute()
            else:
                supabase.table("shift_entries").insert(payload).execute()
            saved += 1
        except Exception as e:
            errors.append({"employee_id": employee_id, "date": it["date"], "error": str(e)[:200]})
            if len(errors) > 50:
                logger.error("sync_jinjer_shifts too many errors")
                break

    logger.info(
        f"sync_jinjer_shifts: month={month} fetched={len(items)} matched_staff={len(code_to_id)} saved={saved} skipped_no_staff={len(skipped_no_staff)} errors={len(errors)}"
    )

    return {
        "month": month,
        "fetched": len(items),
        "matched_staff": len(code_to_id),
        "saved": saved,
        "skipped_no_staff": skipped_no_staff[:50],
        "errors": errors[:50],
    }


@router.post("/attendances/sync")
def sync_jinjer_attendances(
    target_date: str | None = Body(None, embed=True),
    start_date: str | None = Body(None, embed=True),
    end_date: str | None = Body(None, embed=True),
    current_user: dict = Depends(require_admin_or_leader),
):
    if target_date:
        start = target_date
        end = target_date
    else:
        start = start_date or date.today().isoformat()
        end = end_date or start
    return _sync_attendances_for_range(start, end)


@router.post("/attendances/sync-today")
def sync_jinjer_attendances_today(current_user: dict = Depends(require_admin_or_leader)):
    today = date.today().isoformat()
    return _sync_attendances_for_range(today, today)


@router.post("/attendances/cron-sync")
def cron_sync_jinjer_attendances(
    x_cron_key: str | None = Header(None, alias="X-CRON-KEY"),
):
    """
    Render Cron Job 用。
    JWTではなく X-CRON-KEY で認証し、本日のJinjer打刻を同期する。
    """
    _verify_cron_key(x_cron_key)
    today = date.today().isoformat()
    return _sync_attendances_for_range(today, today)
