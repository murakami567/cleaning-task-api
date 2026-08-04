from datetime import date
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.db import supabase
from app.logger import get_logger
from app.routers.jinjer import MONTH_PATTERN, _get_staff_maps, _normalize_time
from app.services.auth_service import require_admin_or_leader
from app.services.jinjer_shift_status_service import fetch_work_schedules_with_status

router = APIRouter(prefix="/jinjer", tags=["jinjer-sync-override"])
logger = get_logger(__name__)


ALLOWED_JINJER_STATUSES = {"出勤", "休み", "定休", "有給"}


def _month_range(month: str) -> tuple[str, str]:
    start = date.fromisoformat(f"{month}-01")
    if start.month == 12:
        end = date(start.year + 1, 1, 1)
    else:
        end = date(start.year, start.month + 1, 1)
    return start.isoformat(), end.isoformat()


def _normalize_jinjer_status(value: Any) -> str:
    status = str(value or "出勤").strip()
    return status if status in ALLOWED_JINJER_STATUSES else "出勤"


@router.post("/shifts/sync")
def sync_jinjer_shifts_override(
    month: str = Body(..., embed=True),
    current_user: dict = Depends(require_admin_or_leader),
):
    """
    Jinjer月次シフトを shift_entries に反映する。

    表示区分は次のルールで統一する。
    - Jinjerの「休み」系: 休み
    - Jinjerの「法定休日」「所定休日」系: 定休
    - Jinjerの「有給」「有給休暇」系: 有給
    - それ以外の勤務予定: 出勤

    対象月・対象スタッフの既存シフトはJinjer取得結果に合わせて差し替える。
    """
    if not MONTH_PATTERN.match(month or ""):
        raise HTTPException(status_code=400, detail="month は YYYY-MM 形式で指定してください。")

    items = fetch_work_schedules_with_status(month)
    code_to_staff, _ = _get_staff_maps()

    matched_items: list[tuple[dict[str, Any], dict[str, Any]]] = []
    skipped_no_staff: list[str] = []
    seen_no_staff: set[str] = set()
    needed_dates: set[str] = set()
    managed_staff_ids: set[str] = set()

    for item in items:
        employee_id = str(item.get("employee_id") or "").strip()
        staff = code_to_staff.get(employee_id)
        if not staff:
            if employee_id and employee_id not in seen_no_staff:
                seen_no_staff.add(employee_id)
                skipped_no_staff.append(employee_id)
            continue

        staff_id = str(staff.get("id") or "")
        shift_date = str(item.get("date") or "")[:10]
        if not staff_id or not shift_date:
            continue

        managed_staff_ids.add(staff_id)
        needed_dates.add(shift_date)
        matched_items.append((item, staff))

    if not matched_items:
        logger.info(
            f"sync_jinjer_shifts_override: month={month} fetched={len(items)} "
            f"matched=0 saved=0 deleted=0 skipped_no_staff={len(skipped_no_staff)}"
        )
        return {
            "month": month,
            "fetched": len(items),
            "matched_staff": 0,
            "saved": 0,
            "deleted_stale": 0,
            "status_counts": {},
            "skipped_no_staff": skipped_no_staff[:50],
            "errors": [],
        }

    month_start, month_end = _month_range(month)

    try:
        day_res = (
            supabase.table("shift_days")
            .select("id, shift_date")
            .gte("shift_date", month_start)
            .lt("shift_date", month_end)
            .execute()
        )
    except Exception as exc:
        logger.error(
            f"sync_jinjer_shifts_override shift_days lookup failed: {exc}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="shift_days 取得に失敗しました。")

    date_to_day_id: dict[str, str] = {}
    for row in day_res.data or []:
        if row.get("shift_date") and row.get("id"):
            date_to_day_id[str(row["shift_date"])] = str(row["id"])

    for shift_date in sorted(needed_dates):
        if shift_date in date_to_day_id:
            continue
        try:
            created = (
                supabase.table("shift_days")
                .insert({"shift_date": shift_date, "note": ""})
                .execute()
            )
            if created.data:
                date_to_day_id[shift_date] = str(created.data[0]["id"])
        except Exception as exc:
            logger.error(
                f"sync_jinjer_shifts_override shift_day create failed: "
                f"date={shift_date} {exc}",
                exc_info=True,
            )

    day_ids = list(date_to_day_id.values())
    try:
        ent_res = (
            supabase.table("shift_entries")
            .select("id, shift_day_id, staff_id, assigned_area, note, status")
            .in_("shift_day_id", day_ids)
            .limit(50000)
            .execute()
            if day_ids
            else None
        )
    except Exception as exc:
        logger.error(
            f"sync_jinjer_shifts_override entries lookup failed: {exc}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="shift_entries 取得に失敗しました。")

    existing: dict[tuple[str, str], dict[str, Any]] = {}
    for row in (ent_res.data if ent_res else []) or []:
        key = (
            str(row.get("shift_day_id") or ""),
            str(row.get("staff_id") or ""),
        )
        if key[0] and key[1]:
            existing[key] = row

    fetched_keys: set[tuple[str, str]] = set()
    saved = 0
    deleted_stale = 0
    status_counts: dict[str, int] = {}
    errors: list[dict[str, Any]] = []

    for item, staff in matched_items:
        staff_id = str(staff.get("id") or "")
        shift_date = str(item.get("date") or "")[:10]
        shift_day_id = date_to_day_id.get(shift_date)
        if not staff_id or not shift_day_id:
            continue

        fetched_keys.add((shift_day_id, staff_id))
        status = _normalize_jinjer_status(item.get("status"))
        start_time = _normalize_time(item.get("start")) if status == "出勤" else None
        end_time = _normalize_time(item.get("end")) if status == "出勤" else None
        previous = existing.get((shift_day_id, staff_id)) or {}
        status_source = str(item.get("status_source") or "").strip()

        previous_note = str(previous.get("note") or "").strip()
        source_note = f"jinjer_sync:{status_source}" if status_source else "jinjer_sync"
        note = previous_note if previous_note and not previous_note.startswith("jinjer_sync") else source_note

        payload = {
            "shift_day_id": shift_day_id,
            "staff_id": staff_id,
            "status": status,
            "start_time": start_time,
            "end_time": end_time,
            "assigned_area": previous.get("assigned_area") or "",
            "note": note,
        }

        try:
            if previous.get("id"):
                (
                    supabase.table("shift_entries")
                    .update(payload)
                    .eq("id", previous["id"])
                    .execute()
                )
            else:
                supabase.table("shift_entries").insert(payload).execute()
            saved += 1
            status_counts[status] = status_counts.get(status, 0) + 1
        except Exception as exc:
            errors.append(
                {
                    "employee_id": item.get("employee_id"),
                    "date": shift_date,
                    "status": status,
                    "error": str(exc)[:200],
                }
            )
            if len(errors) > 50:
                logger.error("sync_jinjer_shifts_override too many upsert errors")
                break

    for (shift_day_id, staff_id), row in existing.items():
        if staff_id not in managed_staff_ids:
            continue
        if (shift_day_id, staff_id) in fetched_keys:
            continue
        try:
            supabase.table("shift_entries").delete().eq("id", row["id"]).execute()
            deleted_stale += 1
        except Exception as exc:
            errors.append(
                {
                    "staff_id": staff_id,
                    "shift_day_id": shift_day_id,
                    "error": str(exc)[:200],
                }
            )
            if len(errors) > 50:
                logger.error("sync_jinjer_shifts_override too many delete errors")
                break

    logger.info(
        f"sync_jinjer_shifts_override: month={month} fetched={len(items)} "
        f"matched_items={len(matched_items)} matched_staff={len(managed_staff_ids)} "
        f"saved={saved} deleted_stale={deleted_stale} statuses={status_counts} "
        f"skipped_no_staff={len(skipped_no_staff)} errors={len(errors)}"
    )

    return {
        "month": month,
        "fetched": len(items),
        "matched_staff": len(managed_staff_ids),
        "saved": saved,
        "deleted_stale": deleted_stale,
        "status_counts": status_counts,
        "skipped_no_staff": skipped_no_staff[:50],
        "errors": errors[:50],
    }
