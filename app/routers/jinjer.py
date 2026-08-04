import io
import os
import re
from datetime import date
from typing import Any

import pandas as pd
from fastapi import APIRouter, Body, Depends, File, Form, Header, HTTPException, UploadFile

from app.db import supabase
from app.logger import get_logger
from app.services.auth_service import require_admin_or_leader
from app.services.jinjer_service import fetch_attendances, fetch_work_schedules

router = APIRouter(prefix="/jinjer", tags=["jinjer"])
logger = get_logger(__name__)


MONTH_PATTERN = re.compile(r"^\d{4}-\d{2}$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RANGE_PATTERN = re.compile(r"(\d{1,2}:\d{2})\s*[~〜\-－]\s*(\d{1,2}:\d{2})")


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).replace("\u3000", " ").strip()


def _normalize_name(value: Any) -> str:
    return re.sub(r"\s+", "", _normalize_text(value))


def _normalize_time(t: str | None) -> str | None:
    """Jinjer は HH:MM:SS で返してくるので HH:MM に丸める。"""
    if not t:
        return None
    s = str(t).strip()
    if not s:
        return None
    if re.match(r"^\d{1}:\d{2}$", s):
        s = f"0{s}"
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


def _get_staff_name_map() -> dict[str, dict]:
    try:
        staff_res = (
            supabase.table("staff_members")
            .select("id, staff_name, staff_code")
            .eq("is_active", True)
            .limit(10000)
            .execute()
        )
    except Exception as e:
        logger.error(f"jinjer staff name lookup failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="スタッフ取得に失敗しました。")

    out: dict[str, dict] = {}
    for row in staff_res.data or []:
        name_key = _normalize_name(row.get("staff_name"))
        if name_key:
            out[name_key] = row
        code_key = _normalize_name(row.get("staff_code"))
        if code_key:
            out[code_key] = row
    return out


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


def _infer_month_from_excel(sheet_name: str | None, df: pd.DataFrame) -> str | None:
    if sheet_name and MONTH_PATTERN.match(str(sheet_name)):
        return str(sheet_name)
    first_cell = _normalize_text(df.iat[0, 0]) if not df.empty else ""
    m = re.search(r"(\d{1,2})\s*月", first_cell)
    if m:
        current_year = date.today().year
        return f"{current_year}-{int(m.group(1)):02d}"
    return None


def _find_date_row(df: pd.DataFrame) -> int:
    best_idx = -1
    best_count = 0
    for idx in range(min(8, len(df))):
        count = 0
        for value in df.iloc[idx].tolist()[1:]:
            text = _normalize_text(value)
            if re.fullmatch(r"\d{1,2}(\.0)?", text):
                n = int(float(text))
                if 1 <= n <= 31:
                    count += 1
        if count > best_count:
            best_idx = idx
            best_count = count
    if best_idx < 0 or best_count < 5:
        raise HTTPException(status_code=400, detail="日付行を検出できませんでした。")
    return best_idx


def _parse_shift_cell(value: Any) -> tuple[str | None, str | None, str | None, str]:
    raw = _normalize_text(value)
    if not raw:
        return None, None, None, raw

    compact = raw.replace(" ", "").replace("　", "")

    if "有休" in compact or "有給" in compact or "年休" in compact:
        return "有給", None, None, raw

    if "法定" in compact or "所定" in compact:
        return "定休", None, None, raw

    if "休み" in compact or compact in {"休", "休日", "公休"}:
        return "休み", None, None, raw

    match = TIME_RANGE_PATTERN.search(raw)
    if match:
        return "出勤", _normalize_time(match.group(1)), _normalize_time(match.group(2)), raw

    if compact in ["出勤", "勤務"]:
        return "出勤", None, None, raw

    return None, None, None, raw


def _parse_shift_file_to_items(file_name: str, content: bytes, month: str | None) -> tuple[str, list[dict[str, Any]]]:
    lower = file_name.lower()
    sheet_name: str | None = None

    if lower.endswith(".csv"):
        try:
            df = pd.read_csv(io.BytesIO(content), header=None, dtype=object, encoding="utf-8-sig")
        except UnicodeDecodeError:
            df = pd.read_csv(io.BytesIO(content), header=None, dtype=object, encoding="cp932")
    elif lower.endswith(".xlsx") or lower.endswith(".xls"):
        excel = pd.ExcelFile(io.BytesIO(content))
        sheet_name = str(excel.sheet_names[0])
        df = pd.read_excel(excel, sheet_name=sheet_name, header=None, dtype=object)
    else:
        raise HTTPException(status_code=400, detail="CSV または Excel ファイルをアップロードしてください。")

    target_month = month or _infer_month_from_excel(sheet_name, df)
    if not target_month or not MONTH_PATTERN.match(target_month):
        raise HTTPException(status_code=400, detail="month は YYYY-MM 形式で指定してください。")

    date_row = _find_date_row(df)
    date_cols: list[tuple[int, str]] = []
    for col in range(1, df.shape[1]):
        text = _normalize_text(df.iat[date_row, col])
        if not re.fullmatch(r"\d{1,2}(\.0)?", text):
            continue
        day = int(float(text))
        if 1 <= day <= 31:
            date_cols.append((col, f"{target_month}-{day:02d}"))

    items: list[dict[str, Any]] = []
    for row_idx in range(date_row + 2, df.shape[0]):
        staff_name = _normalize_text(df.iat[row_idx, 0])
        if not staff_name or staff_name in ["計", "合計"]:
            continue
        for col, shift_date in date_cols:
            status, start, end, raw = _parse_shift_cell(df.iat[row_idx, col])
            if not status:
                continue
            items.append({
                "employee_id": staff_name,
                "staff_name": staff_name,
                "date": shift_date,
                "start": start,
                "end": end,
                "status": status,
                "raw": raw,
            })
    return target_month, items


def _save_shift_items(items: list[dict[str, Any]], source: str) -> dict[str, Any]:
    name_to_staff = _get_staff_name_map()

    needed_dates: set[str] = set()
    matched_items: list[tuple[dict[str, Any], dict[str, Any]]] = []
    skipped_no_staff: list[str] = []
    seen_no_staff: set[str] = set()

    for it in items:
        employee_id = _normalize_text(it.get("employee_id"))
        staff = name_to_staff.get(_normalize_name(employee_id))
        if not staff:
            if employee_id and employee_id not in seen_no_staff:
                seen_no_staff.add(employee_id)
                skipped_no_staff.append(employee_id)
            continue
        if it.get("date"):
            needed_dates.add(it["date"])
            matched_items.append((it, staff))

    try:
        day_res = (
            supabase.table("shift_days")
            .select("id, shift_date")
            .in_("shift_date", list(needed_dates))
            .execute()
        ) if needed_dates else None
    except Exception as e:
        logger.error(f"shift upload shift_days lookup failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="shift_days 取得に失敗しました。")

    date_to_day_id: dict[str, str] = {}
    for row in (day_res.data if day_res else []) or []:
        date_to_day_id[row["shift_date"]] = row["id"]

    for d in sorted(needed_dates):
        if d in date_to_day_id:
            continue
        try:
            created = supabase.table("shift_days").insert({"shift_date": d, "note": ""}).execute()
            if created.data:
                date_to_day_id[d] = created.data[0]["id"]
        except Exception as e:
            logger.error(f"shift upload shift_day create failed: date={d} {e}", exc_info=True)

    try:
        ent_res = (
            supabase.table("shift_entries")
            .select("id, shift_day_id, staff_id, assigned_area, note, status")
            .in_("shift_day_id", list(date_to_day_id.values()))
            .limit(50000)
            .execute()
        ) if date_to_day_id else None
    except Exception as e:
        logger.error(f"shift upload entries lookup failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="shift_entries 取得に失敗しました。")

    existing: dict[tuple[str, str], dict[str, Any]] = {}
    for row in (ent_res.data if ent_res else []) or []:
        existing[(row["shift_day_id"], row["staff_id"])] = row

    saved = 0
    status_counts: dict[str, int] = {}
    errors: list[dict[str, Any]] = []

    for it, staff in matched_items:
        staff_id = staff.get("id")
        shift_day_id = date_to_day_id.get(it["date"])
        if not staff_id or not shift_day_id:
            continue

        status = str(it.get("status") or "出勤")
        is_off = status in {"休み", "定休", "有給"}
        start_time = None if is_off else _normalize_time(it.get("start"))
        end_time = None if is_off else _normalize_time(it.get("end"))
        prev = existing.get((shift_day_id, staff_id)) or {}
        payload = {
            "shift_day_id": shift_day_id,
            "staff_id": staff_id,
            "status": status,
            "start_time": start_time,
            "end_time": end_time,
            "assigned_area": prev.get("assigned_area") or "",
            "note": prev.get("note") or (f"{source}: {it.get('raw')}" if it.get("raw") else source),
        }

        try:
            if prev.get("id"):
                supabase.table("shift_entries").update(payload).eq("id", prev["id"]).execute()
            else:
                inserted = supabase.table("shift_entries").insert(payload).execute()
                if inserted.data:
                    existing[(shift_day_id, staff_id)] = inserted.data[0]
            saved += 1
            status_counts[status] = status_counts.get(status, 0) + 1
        except Exception as e:
            errors.append({"employee_id": it.get("employee_id"), "date": it.get("date"), "error": str(e)[:200]})
            if len(errors) > 50:
                logger.error("shift upload too many errors")
                break

    return {
        "fetched": len(items),
        "matched_staff": len({str(staff.get("id")) for _, staff in matched_items if staff.get("id")}),
        "saved": saved,
        "status_counts": status_counts,
        "skipped_no_staff": skipped_no_staff[:50],
        "errors": errors[:50],
    }


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


@router.post("/shifts/upload")
async def upload_shift_file(
    file: UploadFile = File(...),
    month: str | None = Form(None),
    current_user: dict = Depends(require_admin_or_leader),
):
    content = await file.read()
    target_month, items = _parse_shift_file_to_items(file.filename or "", content, month)
    result = _save_shift_items(items, source="jinjer_excel_upload")
    logger.info(
        f"upload_shift_file: month={target_month} file={file.filename} fetched={result['fetched']} saved={result['saved']} status_counts={result.get('status_counts', {})} skipped_no_staff={len(result['skipped_no_staff'])} errors={len(result['errors'])}"
    )
    return {"month": target_month, "file_name": file.filename, **result}


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
