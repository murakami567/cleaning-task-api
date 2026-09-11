from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.db import supabase
from app.logger import get_logger
from app.services.auth_service import require_admin_or_leader
from app.routers.jinjer import (
    _get_staff_name_map,
    _normalize_name,
    _normalize_text,
    _normalize_time,
    _parse_shift_file_to_items,
)

router = APIRouter(prefix="/jinjer", tags=["jinjer-shift-upload"])
logger = get_logger(__name__)

BATCH_SIZE = 200
MAX_ERROR_DETAILS = 100


def _chunks(rows: list[dict[str, Any]], size: int = BATCH_SIZE):
    for index in range(0, len(rows), size):
        yield rows[index : index + size]


def _save_update_batch(rows: list[dict[str, Any]], errors: list[dict[str, Any]]) -> int:
    """既存行は主キー id を含めて一括 upsert。失敗時だけ1件ずつ再試行する。"""
    if not rows:
        return 0
    saved = 0
    for chunk in _chunks(rows):
        try:
            supabase.table("shift_entries").upsert(chunk).execute()
            saved += len(chunk)
        except Exception as batch_error:
            logger.warning(f"shift upload bulk update fallback: rows={len(chunk)} error={batch_error}")
            for row in chunk:
                row_id = row.get("id")
                payload = {k: v for k, v in row.items() if k != "id"}
                try:
                    supabase.table("shift_entries").update(payload).eq("id", row_id).execute()
                    saved += 1
                except Exception as e:
                    errors.append({
                        "staff_id": row.get("staff_id"),
                        "shift_day_id": row.get("shift_day_id"),
                        "error": str(e)[:300],
                    })
    return saved


def _save_insert_batch(rows: list[dict[str, Any]], errors: list[dict[str, Any]]) -> int:
    """新規行は200件単位で一括 insert。失敗時だけ1件ずつ再試行する。"""
    if not rows:
        return 0
    saved = 0
    for chunk in _chunks(rows):
        try:
            supabase.table("shift_entries").insert(chunk).execute()
            saved += len(chunk)
        except Exception as batch_error:
            logger.warning(f"shift upload bulk insert fallback: rows={len(chunk)} error={batch_error}")
            for row in chunk:
                try:
                    supabase.table("shift_entries").insert(row).execute()
                    saved += 1
                except Exception as e:
                    errors.append({
                        "staff_id": row.get("staff_id"),
                        "shift_day_id": row.get("shift_day_id"),
                        "error": str(e)[:300],
                    })
    return saved


def _save_shift_items_bulk(items: list[dict[str, Any]], source: str) -> dict[str, Any]:
    name_to_staff = _get_staff_name_map()

    needed_dates: set[str] = set()
    matched_items: list[tuple[dict[str, Any], dict[str, Any]]] = []
    skipped_no_staff: list[str] = []
    seen_no_staff: set[str] = set()

    for item in items:
        employee_id = _normalize_text(item.get("employee_id"))
        staff = name_to_staff.get(_normalize_name(employee_id))
        if not staff:
            if employee_id and employee_id not in seen_no_staff:
                seen_no_staff.add(employee_id)
                skipped_no_staff.append(employee_id)
            continue
        if item.get("date"):
            needed_dates.add(str(item["date"]))
            matched_items.append((item, staff))

    date_to_day_id: dict[str, str] = {}
    errors: list[dict[str, Any]] = []

    if needed_dates:
        try:
            day_res = (
                supabase.table("shift_days")
                .select("id, shift_date")
                .in_("shift_date", sorted(needed_dates))
                .execute()
            )
            for row in day_res.data or []:
                date_to_day_id[str(row["shift_date"])] = str(row["id"])
        except Exception as e:
            logger.exception("shift upload shift_days lookup failed")
            raise

    # 最大31日なのでここは逐次作成でも十分小さい。
    for target_date in sorted(needed_dates):
        if target_date in date_to_day_id:
            continue
        try:
            created = supabase.table("shift_days").insert({"shift_date": target_date, "note": ""}).execute()
            if created.data:
                date_to_day_id[target_date] = str(created.data[0]["id"])
            else:
                errors.append({"date": target_date, "error": "shift_day create returned no data"})
        except Exception as e:
            errors.append({"date": target_date, "error": str(e)[:300]})

    existing: dict[tuple[str, str], dict[str, Any]] = {}
    if date_to_day_id:
        try:
            ent_res = (
                supabase.table("shift_entries")
                .select("id, shift_day_id, staff_id, assigned_area, note, status")
                .in_("shift_day_id", list(date_to_day_id.values()))
                .limit(50000)
                .execute()
            )
            for row in ent_res.data or []:
                existing[(str(row["shift_day_id"]), str(row["staff_id"]))] = row
        except Exception:
            logger.exception("shift upload entries lookup failed")
            raise

    update_rows: list[dict[str, Any]] = []
    insert_rows: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}
    prepared_count = 0

    for item, staff in matched_items:
        staff_id = str(staff.get("id") or "")
        shift_day_id = date_to_day_id.get(str(item.get("date") or ""))
        if not staff_id or not shift_day_id:
            errors.append({
                "employee_id": item.get("employee_id"),
                "date": item.get("date"),
                "error": "staff_id or shift_day_id missing",
            })
            continue

        status = str(item.get("status") or "出勤")
        is_off = status in {"休み", "定休", "有給"}
        previous = existing.get((shift_day_id, staff_id)) or {}
        payload: dict[str, Any] = {
            "shift_day_id": shift_day_id,
            "staff_id": staff_id,
            "status": status,
            "start_time": None if is_off else _normalize_time(item.get("start")),
            "end_time": None if is_off else _normalize_time(item.get("end")),
            "assigned_area": previous.get("assigned_area") or "",
            "note": previous.get("note") or (f"{source}: {item.get('raw')}" if item.get("raw") else source),
        }

        if previous.get("id"):
            update_rows.append({"id": previous["id"], **payload})
        else:
            insert_rows.append(payload)

        prepared_count += 1
        status_counts[status] = status_counts.get(status, 0) + 1

    saved_updates = _save_update_batch(update_rows, errors)
    saved_inserts = _save_insert_batch(insert_rows, errors)
    saved = saved_updates + saved_inserts

    matched_staff_ids = {str(staff.get("id")) for _, staff in matched_items if staff.get("id")}
    error_count = len(errors)

    logger.info(
        "shift upload bulk save: "
        f"fetched={len(items)} matched_items={len(matched_items)} matched_staff={len(matched_staff_ids)} "
        f"prepared={prepared_count} updates={len(update_rows)} inserts={len(insert_rows)} "
        f"saved={saved} skipped_no_staff={len(skipped_no_staff)} errors={error_count}"
    )

    return {
        "fetched": len(items),
        "matched_items": len(matched_items),
        "matched_staff": len(matched_staff_ids),
        "prepared": prepared_count,
        "saved": saved,
        "updated": saved_updates,
        "inserted": saved_inserts,
        "status_counts": status_counts,
        "skipped_no_staff_count": len(skipped_no_staff),
        "skipped_no_staff": skipped_no_staff[:MAX_ERROR_DETAILS],
        "error_count": error_count,
        "errors": errors[:MAX_ERROR_DETAILS],
    }


@router.post("/shifts/upload")
async def upload_shift_file_bulk(
    file: UploadFile = File(...),
    month: str | None = Form(None),
    current_user: dict = Depends(require_admin_or_leader),
):
    content = await file.read()
    target_month, items = _parse_shift_file_to_items(file.filename or "", content, month)
    result = _save_shift_items_bulk(items, source="jinjer_excel_upload")
    logger.info(
        f"upload_shift_file_bulk: month={target_month} file={file.filename} "
        f"fetched={result['fetched']} matched_staff={result['matched_staff']} saved={result['saved']} "
        f"skipped_no_staff={result['skipped_no_staff_count']} errors={result['error_count']}"
    )
    return {"month": target_month, "file_name": file.filename, **result}
