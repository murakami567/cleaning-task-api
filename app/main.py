import os
import csv
import io
import requests
from datetime import date, datetime, timedelta
from calendar import monthrange

from fastapi import FastAPI, Body, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Body, HTTPException

from app.db import supabase


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://cleaning-task-admin.onrender.com",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================================================
# 基本
# =========================================================
@app.get("/")
def root():
    return {"status": "ok"}


# =========================================================
# 清掃タスク
# =========================================================
@app.get("/tasks/today")
def get_today_tasks():
    today = date.today().isoformat()

    res = (
        supabase.table("cleaning_tasks")
        .select("*")
        .eq("task_date", today)
        .execute()
    )

    return res.data


@app.get("/tasks/future")
def get_future_tasks():
    today = date.today().isoformat()

    res = (
        supabase.table("cleaning_tasks")
        .select("*")
        .gt("task_date", today)
        .order("task_date")
        .execute()
    )

    return res.data


@app.post("/tasks/create")
def create_task(
    property_name: str = Body(...),
    room_name: str = Body(...),
    room_key: str = Body(...),
    task_date: str = Body(...),
    status: str = Body("未着手"),
    note: str = Body(""),
):
    payload = {
        "property_name": property_name,
        "room_name": room_name,
        "room_key": room_key,
        "task_date": task_date,
        "checkout_date": task_date,
        "next_checkin_date": None,
        "gap_nights": 0,
        "guest_count": 0,
        "load_score": 0,
        "status": status,
        "note": note,
        "source": "manual",
    }

    res = supabase.table("cleaning_tasks").insert(payload).execute()

    if not res.data:
        raise HTTPException(status_code=500, detail="task creation failed")

    return res.data[0]

@app.post("/tasks/update")
def update_task(
    task_id: str = Body(...),
    status: str | None = Body(None),
    note: str | None = Body(None),

    assigned_staff_ids: list[str] | None = Body(None),
    assigned_staff_names: list[str] | None = Body(None),

    assigned_staff_id: str | None = Body(None),
    assigned_staff_name: str | None = Body(None),
):
    payload = {}

    if status is not None:
        payload["status"] = status

    if note is not None:
        payload["note"] = note

    if assigned_staff_ids is not None:
        payload["assigned_staff_ids"] = assigned_staff_ids

    if assigned_staff_names is not None:
        payload["assigned_staff_names"] = assigned_staff_names

    if assigned_staff_id is not None:
        payload["assigned_staff_id"] = assigned_staff_id

    if assigned_staff_name is not None:
        payload["assigned_staff_name"] = assigned_staff_name

    # 配列が来たら単数列にも先頭を入れる
    if assigned_staff_ids is not None:
        payload["assigned_staff_id"] = assigned_staff_ids[0] if len(assigned_staff_ids) > 0 else None

    if assigned_staff_names is not None:
        payload["assigned_staff_name"] = assigned_staff_names[0] if len(assigned_staff_names) > 0 else None

    if not payload:
        raise HTTPException(status_code=400, detail="no update fields")

    try:
        res = (
            supabase.table("cleaning_tasks")
            .update(payload)
            .eq("id", task_id)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"supabase update failed: {str(e)}")

    return {
        "ok": True,
        "task_id": task_id,
        "updated": payload,
        "data": res.data,
    }

# =========================================================
# 物件一覧 / 物件管理
# =========================================================
@app.get("/properties")
def get_properties():
    res = (
        supabase.table("properties")
        .select("*")
        .order("sort_order")
        .order("property_name")
        .execute()
    )
    return res.data


@app.post("/properties/create")
def create_property(
    property_code: str = Body(...),
    property_name: str = Body(...),
    normalized_name: str = Body(""),
    sort_order: int = Body(999),
    is_active: bool = Body(True),
):
    payload = {
        "property_code": property_code,
        "property_name": property_name,
        "normalized_name": normalized_name if normalized_name else property_name,
        "sort_order": sort_order,
        "is_active": is_active,
    }

    res = supabase.table("properties").insert(payload).execute()

    if not res.data:
        raise HTTPException(status_code=500, detail="property creation failed")

    return res.data[0]


@app.post("/properties/update")
def update_property(
    property_id: str = Body(...),
    property_code: str = Body(...),
    property_name: str = Body(...),
    normalized_name: str = Body(""),
    sort_order: int = Body(999),
    is_active: bool = Body(True),
):
    payload = {
        "property_code": property_code,
        "property_name": property_name,
        "normalized_name": normalized_name if normalized_name else property_name,
        "sort_order": sort_order,
        "is_active": is_active,
    }

    res = (
        supabase.table("properties")
        .update(payload)
        .eq("id", property_id)
        .execute()
    )

    if not res.data:
        raise HTTPException(status_code=500, detail="property update failed")

    return res.data[0]


# =========================================================
# 部屋一覧 / 部屋管理
# =========================================================
@app.get("/rooms")
def get_rooms(property_id: str | None = None):
    query = (
        supabase.table("rooms")
        .select("*")
        .order("room_sort_order")
        .order("room_name")
    )

    if property_id:
        query = query.eq("property_id", property_id)

    res = query.execute()
    return res.data


@app.post("/rooms/create")
def create_room(
    property_id: str = Body(...),
    room_name: str = Body(...),
    room_code: str = Body(""),
    room_key: str = Body(...),
    normalized_room_key: str = Body(""),
    capacity: int = Body(1),
    room_sort_order: int = Body(999),
    is_active: bool = Body(True),
):
    payload = {
        "property_id": property_id,
        "room_name": room_name,
        "room_code": room_code if room_code else room_name,
        "room_key": room_key,
        "normalized_room_key": normalized_room_key if normalized_room_key else room_key,
        "capacity": capacity,
        "room_sort_order": room_sort_order,
        "is_active": is_active,
    }

    res = supabase.table("rooms").insert(payload).execute()

    if not res.data:
        raise HTTPException(status_code=500, detail="room creation failed")

    return res.data[0]


@app.post("/rooms/update")
def update_room(
    room_id: str = Body(...),
    property_id: str = Body(...),
    room_name: str = Body(...),
    room_code: str = Body(""),
    room_key: str = Body(...),
    normalized_room_key: str = Body(""),
    capacity: int = Body(1),
    room_sort_order: int = Body(999),
    is_active: bool = Body(True),
):
    payload = {
        "property_id": property_id,
        "room_name": room_name,
        "room_code": room_code if room_code else room_name,
        "room_key": room_key,
        "normalized_room_key": normalized_room_key if normalized_room_key else room_key,
        "capacity": capacity,
        "room_sort_order": room_sort_order,
        "is_active": is_active,
    }

    res = (
        supabase.table("rooms")
        .update(payload)
        .eq("id", room_id)
        .execute()
    )

    if not res.data:
        raise HTTPException(status_code=500, detail="room update failed")

    return res.data[0]


# =========================================================
# 新規オープン進捗
# =========================================================
@app.get("/openings")
def get_openings():
    res = (
        supabase.table("opening_projects")
        .select("*")
        .order("due_date")
        .execute()
    )
    return res.data


@app.post("/openings/create")
def create_opening(
    property_id: str | None = Body(None),
    property_name: str = Body(...),
    room_name: str = Body(""),
    title: str = Body(...),
    owner_name: str = Body(""),
    due_date: str | None = Body(None),
    status: str = Body("未着手"),
    priority: str = Body("中"),
    progress: int = Body(0),
    memo: str = Body(""),
):
    payload = {
        "property_id": property_id,
        "property_name": property_name,
        "room_name": room_name,
        "title": title,
        "owner_name": owner_name,
        "due_date": due_date,
        "status": status,
        "priority": priority,
        "progress": progress,
        "memo": memo,
    }

    res = supabase.table("opening_projects").insert(payload).execute()

    if not res.data:
        raise HTTPException(status_code=500, detail="opening creation failed")

    return res.data[0]


@app.post("/openings/update")
def update_opening(
    opening_id: str = Body(...),
    property_id: str | None = Body(None),
    property_name: str = Body(...),
    room_name: str = Body(""),
    title: str = Body(...),
    owner_name: str = Body(""),
    due_date: str | None = Body(None),
    status: str = Body("未着手"),
    priority: str = Body("中"),
    progress: int = Body(0),
    memo: str = Body(""),
):
    payload = {
        "property_id": property_id,
        "property_name": property_name,
        "room_name": room_name,
        "title": title,
        "owner_name": owner_name,
        "due_date": due_date,
        "status": status,
        "priority": priority,
        "progress": progress,
        "memo": memo,
    }

    res = (
        supabase.table("opening_projects")
        .update(payload)
        .eq("id", opening_id)
        .execute()
    )

    if not res.data:
        raise HTTPException(status_code=500, detail="opening update failed")

    return res.data[0]


# =========================================================
# 設備管理
# =========================================================
@app.get("/facilities")
def get_facilities():
    res = (
        supabase.table("facility_tasks")
        .select("*")
        .order("start_date")
        .execute()
    )
    return res.data


@app.post("/facilities/create")
def create_facility(
    property_id: str | None = Body(None),
    property_name: str = Body(...),
    room_name: str = Body(""),
    assignee: str = Body(""),
    content: str = Body(...),
    start_date: str | None = Body(None),
    end_date: str | None = Body(None),
    status: str = Body("未着手"),
    note: str = Body(""),
):
    payload = {
        "property_id": property_id,
        "property_name": property_name,
        "room_name": room_name,
        "assignee": assignee,
        "content": content,
        "start_date": start_date,
        "end_date": end_date,
        "status": status,
        "note": note,
    }

    res = supabase.table("facility_tasks").insert(payload).execute()

    if not res.data:
        raise HTTPException(status_code=500, detail="facility creation failed")

    return res.data[0]


@app.post("/facilities/update")
def update_facility(
    facility_id: str = Body(...),
    property_id: str | None = Body(None),
    property_name: str = Body(...),
    room_name: str = Body(""),
    assignee: str = Body(""),
    content: str = Body(...),
    start_date: str | None = Body(None),
    end_date: str | None = Body(None),
    status: str = Body("未着手"),
    note: str = Body(""),
):
    payload = {
        "property_id": property_id,
        "property_name": property_name,
        "room_name": room_name,
        "assignee": assignee,
        "content": content,
        "start_date": start_date,
        "end_date": end_date,
        "status": status,
        "note": note,
    }

    res = (
        supabase.table("facility_tasks")
        .update(payload)
        .eq("id", facility_id)
        .execute()
    )

    if not res.data:
        raise HTTPException(status_code=500, detail="facility update failed")

    return res.data[0]


# =========================================================
# スタッフ
# =========================================================
@app.get("/staffs")
def get_staffs():
    res = (
        supabase.table("staff_members")
        .select("*")
        .order("sort_order")
        .order("staff_name")
        .execute()
    )
    return res.data


@app.post("/staffs/create")
def create_staff(
    staff_code: str = Body(...),
    staff_name: str = Body(...),
    role: str = Body("staff"),
    is_active: bool = Body(True),
    sort_order: int = Body(999),
    note: str = Body(""),
):
    payload = {
        "staff_code": staff_code,
        "staff_name": staff_name,
        "role": role,
        "is_active": is_active,
        "sort_order": sort_order,
        "note": note,
    }

    res = supabase.table("staff_members").insert(payload).execute()

    if not res.data:
        raise HTTPException(status_code=500, detail="staff creation failed")

    return res.data[0]


@app.post("/staffs/update")
def update_staff(
    staff_id: str = Body(...),
    staff_code: str = Body(...),
    staff_name: str = Body(...),
    role: str = Body("staff"),
    is_active: bool = Body(True),
    sort_order: int = Body(999),
    note: str = Body(""),
):
    payload = {
        "staff_code": staff_code,
        "staff_name": staff_name,
        "role": role,
        "is_active": is_active,
        "sort_order": sort_order,
        "note": note,
    }

    res = (
        supabase.table("staff_members")
        .update(payload)
        .eq("id", staff_id)
        .execute()
    )

    if not res.data:
        raise HTTPException(status_code=500, detail="staff update failed")

    return res.data[0]


# =========================================================
# シフト管理
# =========================================================
@app.get("/shifts")
def get_shifts(shift_date: str | None = None):
    query = (
        supabase.table("shift_days")
        .select("*, shift_entries(*, staff_members(*))")
        .order("shift_date")
    )

    if shift_date:
        query = query.eq("shift_date", shift_date)

    res = query.execute()
    return res.data


@app.post("/shifts/create_day")
def create_shift_day(
    shift_date: str = Body(...),
    note: str = Body(""),
):
    payload = {
        "shift_date": shift_date,
        "note": note,
    }

    res = supabase.table("shift_days").insert(payload).execute()

    if not res.data:
        raise HTTPException(status_code=500, detail="shift day creation failed")

    return res.data[0]


@app.post("/shifts/get_or_create_day")
def get_or_create_shift_day(
    shift_date: str = Body(...),
    note: str = Body(""),
):
    existing = (
        supabase.table("shift_days")
        .select("*")
        .eq("shift_date", shift_date)
        .execute()
    )

    if existing.data:
        return existing.data[0]

    created = (
        supabase.table("shift_days")
        .insert({
            "shift_date": shift_date,
            "note": note,
        })
        .execute()
    )

    if not created.data:
        raise HTTPException(status_code=500, detail="shift day create failed")

    return created.data[0]


@app.post("/shifts/upsert_entry")
def upsert_shift_entry(
    shift_day_id: str = Body(...),
    staff_id: str = Body(...),
    status: str = Body("出勤"),
    start_time: str | None = Body(None),
    end_time: str | None = Body(None),
    assigned_area: str = Body(""),
    note: str = Body(""),
):
    existing = (
        supabase.table("shift_entries")
        .select("*")
        .eq("shift_day_id", shift_day_id)
        .eq("staff_id", staff_id)
        .execute()
    )

    payload = {
        "shift_day_id": shift_day_id,
        "staff_id": staff_id,
        "status": status,
        "start_time": start_time,
        "end_time": end_time,
        "assigned_area": assigned_area,
        "note": note,
    }

    if existing.data:
        res = (
            supabase.table("shift_entries")
            .update(payload)
            .eq("id", existing.data[0]["id"])
            .execute()
        )
    else:
        res = supabase.table("shift_entries").insert(payload).execute()

    if not res.data:
        raise HTTPException(status_code=500, detail="shift entry upsert failed")

    return res.data[0]


# =========================================================
# 月間シフト表
# =========================================================
@app.get("/shift-board")
def get_shift_board(year: int, month: int):
    start_date = f"{year}-{month:02d}-01"
    last_day = monthrange(year, month)[1]
    end_date = f"{year}-{month:02d}-{last_day:02d}"

    staffs_res = (
        supabase.table("staff_members")
        .select("*")
        .eq("is_active", True)
        .order("sort_order")
        .order("staff_name")
        .execute()
    )

    shift_days_res = (
        supabase.table("shift_days")
        .select("*, shift_entries(*)")
        .gte("shift_date", start_date)
        .lte("shift_date", end_date)
        .order("shift_date")
        .execute()
    )

    return {
        "staffs": staffs_res.data,
        "days": shift_days_res.data,
    }


# =========================================================
# Beds24 CSV 同期
# =========================================================
BEDS24_CSV_URL = os.getenv("BEDS24_CSV_URL", "https://www.beds24.com/api/csv/getbookingscsv")
BEDS24_CSV_USERNAME = os.getenv("BEDS24_CSV_USERNAME", "")
BEDS24_CSV_PASSWORD = os.getenv("BEDS24_CSV_PASSWORD", "")


def format_jst_date_string(dt: date) -> str:
    return dt.strftime("%Y-%m-%d")


def normalize_property_name_csv(name: str) -> str:
    if not name:
        return ""

    hotel_map = {
        "FFFホテル": "FFFホテル",
        "美野島A": "美野島",
        "美野島B": "美野島",
        "冷泉A": "冷泉",
        "冷泉B": "冷泉",
        "西中洲A": "西中洲",
        "西中洲B": "西中洲",
    }

    for k, v in hotel_map.items():
        if k in str(name):
            return v

    return str(name).strip()


def parse_csv_text(csv_text: str):
    reader = csv.reader(io.StringIO(csv_text))
    return list(reader)


def find_target_columns(header_row):
    target_columns = {
        "Title": -1,
        "Property": -1,
        "Unit": -1,
        "FirstNight": -1,
        "Check Out": -1,
        "Price": -1,
        "Status": -1,
        "Referer": -1,
        "Adult": -1,
        "Child": -1,
        "Time Entered": -1,
        "Full Name": -1,
    }

    for i, h in enumerate(header_row):
        header = str(h).strip()
        if header in target_columns:
            target_columns[header] = i

    required = [
        "Title",
        "Property",
        "Unit",
        "FirstNight",
        "Check Out",
        "Price",
        "Status",
        "Referer",
        "Adult",
        "Child",
        "Time Entered",
    ]

    missing = [k for k in required if target_columns[k] == -1]
    if missing:
        raise HTTPException(
            status_code=500,
            detail=f"required csv columns missing: {', '.join(missing)}"
        )

    return target_columns


def safe_get(row, idx):
    if idx < 0 or idx >= len(row):
        return ""
    return row[idx]


def calc_load_score(guest_count: int, gap_nights: int) -> int:
    score = guest_count or 0

    if gap_nights == 0:
        score += 2
    elif gap_nights == 1:
        score += 1

    return score


def parse_beds24_date(date_str: str | None):
    """
    Beds24 CSVの日付文字列を yyyy-mm-dd に正規化
    対応例:
    - 2026-03-24
    - 28 Mar 2026
    - 28 Mar 2026 00:00
    """
    if not date_str:
        return None

    s = str(date_str).strip()
    if not s:
        return None

    for fmt in ("%Y-%m-%d", "%d %b %Y", "%d %b %Y %H:%M"):
        try:
            if fmt == "%Y-%m-%d":
                return datetime.fromisoformat(s[:10]).date().isoformat()
            return datetime.strptime(s.replace(",", ""), fmt).date().isoformat()
        except Exception:
            pass

    return None


@app.get("/beds24/csv/test")
def beds24_csv_test():
    if not BEDS24_CSV_USERNAME or not BEDS24_CSV_PASSWORD:
        raise HTTPException(
            status_code=500,
            detail="BEDS24_CSV_USERNAME or BEDS24_CSV_PASSWORD is not set"
        )

    today = date.today()
    next_month_end = date(today.year, today.month, 1) + timedelta(days=62)
    next_month_end = date(next_month_end.year, next_month_end.month, 1) - timedelta(days=1)

    payload = {
        "username": BEDS24_CSV_USERNAME,
        "password": BEDS24_CSV_PASSWORD,
        "datefrom": format_jst_date_string(today),
        "dateto": format_jst_date_string(next_month_end),
    }

    try:
        res = requests.post(BEDS24_CSV_URL, data=payload, timeout=30)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Beds24 CSV request failed: {str(e)}")

    return {
        "status_code": res.status_code,
        "content_type": res.headers.get("content-type"),
        "text_head": res.text[:1500],
    }


@app.post("/beds24/csv/sync")
def beds24_csv_sync(
    from_date: str | None = Body(default=None),
    to_date: str | None = Body(default=None),
):
    if not BEDS24_CSV_USERNAME or not BEDS24_CSV_PASSWORD:
        raise HTTPException(
            status_code=500,
            detail="BEDS24_CSV_USERNAME or BEDS24_CSV_PASSWORD is not set"
        )

    today = date.today()

    if from_date and from_date != "string":
        start_date_str = from_date
    else:
        start_date_str = format_jst_date_string(today)

    if to_date and to_date != "string":
        end_date_str = to_date
    else:
        next_month_end = date(today.year, today.month, 1) + timedelta(days=62)
        next_month_end = date(next_month_end.year, next_month_end.month, 1) - timedelta(days=1)
        end_date_str = format_jst_date_string(next_month_end)

    payload = {
        "username": BEDS24_CSV_USERNAME,
        "password": BEDS24_CSV_PASSWORD,
        "datefrom": start_date_str,
        "dateto": end_date_str,
    }

    try:
        res = requests.post(BEDS24_CSV_URL, data=payload, timeout=60)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Beds24 CSV request failed: {str(e)}")

    if res.status_code != 200:
        raise HTTPException(status_code=res.status_code, detail=res.text)

    try:
        csv_rows = parse_csv_text(res.text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"CSV parse failed: {str(e)}")

    if not csv_rows:
        return {
            "from": start_date_str,
            "to": end_date_str,
            "csv_row_count": 0,
            "raw_saved_count": 0,
            "processed_saved_count": 0,
            "cleaning_saved_count": 0,
            "skipped_count": 0,
            "raw_saved": [],
            "processed_saved": [],
            "cleaning_saved": [],
            "skipped": [],
        }

    target_columns = find_target_columns(csv_rows[0])

    raw_saved = []
    processed_saved = []
    cleaning_saved = []
    skipped = []

    for i in range(1, len(csv_rows)):
        row = csv_rows[i]

        try:
            title = safe_get(row, target_columns["Title"])
            status = safe_get(row, target_columns["Status"])
            property_name_raw = safe_get(row, target_columns["Property"])
            unit_raw = safe_get(row, target_columns["Unit"])
            full_name = safe_get(row, target_columns["Full Name"])
            first_night_raw = safe_get(row, target_columns["FirstNight"])
            check_out_raw = safe_get(row, target_columns["Check Out"])
            price = safe_get(row, target_columns["Price"])
            referer = safe_get(row, target_columns["Referer"])
            adult_raw = safe_get(row, target_columns["Adult"])
            child_raw = safe_get(row, target_columns["Child"])
            time_entered = safe_get(row, target_columns["Time Entered"])

            checkin_date = parse_beds24_date(first_night_raw)
            checkout_date = parse_beds24_date(check_out_raw)

            raw_booking_id = f"{property_name_raw}_{unit_raw}_{first_night_raw}_{check_out_raw}_{i}"

            # 1. raw 保存
            raw_payload = {
                "booking_id": raw_booking_id,
                "raw_json": {
                    "Title": title,
                    "Property": property_name_raw,
                    "Unit": unit_raw,
                    "FirstNight": first_night_raw,
                    "Check Out": check_out_raw,
                    "Price": price,
                    "Status": status,
                    "Referer": referer,
                    "Adult": adult_raw,
                    "Child": child_raw,
                    "Time Entered": time_entered,
                    "Full Name": full_name,
                },
            }

            try:
                raw_res = supabase.table("beds24_raw_bookings").insert(raw_payload).execute()
                raw_saved.append({
                    "booking_id": raw_booking_id,
                    "raw_count": len(raw_res.data or []),
                })
            except Exception as e:
                skipped.append({
                    "reason": f"raw save error: {str(e)}",
                    "booking_id": raw_booking_id,
                })
                continue

            # 2. GAS準拠除外
            if status == "Cancelled":
                skipped.append({
                    "reason": "cancelled",
                    "booking_id": raw_booking_id,
                })
                continue

            if "ブロック" in title:
                skipped.append({
                    "reason": "blocked_by_title",
                    "booking_id": raw_booking_id,
                })
                continue

            if "予備部屋" in title:
                skipped.append({
                    "reason": "reserve_room_by_title",
                    "booking_id": raw_booking_id,
                })
                continue

            # 3. 加工
            property_name_normalized, unit_normalized = split_property_and_room(
    property_name_raw,
    unit_raw
)

            property_unit_key = f"{property_name_raw}{unit_normalized}"
            room_key = f"{property_name_normalized}{unit_normalized}"

            try:
                adult_count = int(adult_raw or 0)
            except Exception:
                adult_count = 0

            try:
                child_count = int(child_raw or 0)
            except Exception:
                child_count = 0

            guest_count = adult_count + child_count

            processed_payload = {
                "booking_id": raw_booking_id,
                "full_name": full_name,
                "title_raw": title,
                "property_name_raw": property_name_raw,
                "property_name_normalized": property_name_normalized,
                "room_name_raw": unit_raw,
                "room_name_normalized": unit_normalized,
                "room_key": room_key,
                "checkin_date": checkin_date,
                "checkout_date": checkout_date,
                "price": price,
                "status_raw": status,
                "referer": referer,
                "adult_count": adult_count,
                "child_count": child_count,
                "guest_count": guest_count,
                "time_entered": time_entered,
                "property_unit_key": property_unit_key,
                "is_cancelled": False,
                "is_blocked": False,
            }

            try:
                processed_res = (
                    supabase.table("beds24_processed_bookings")
                    .upsert(processed_payload, on_conflict="booking_id")
                    .execute()
                )
                processed_saved.append({
                    "booking_id": raw_booking_id,
                    "processed_count": len(processed_res.data or []),
                })
            except Exception as e:
                skipped.append({
                    "reason": f"processed save error: {str(e)}",
                    "booking_id": raw_booking_id,
                })
                continue

            # 4. cleaning_tasks 生成
            if not checkout_date:
                skipped.append({
                    "reason": "missing checkout",
                    "booking_id": raw_booking_id,
                    "check_out_raw": check_out_raw,
                })
                continue

            gap_nights = 0
            if checkout_date and checkin_date:
                try:
                    co = datetime.fromisoformat(checkout_date)
                    ci = datetime.fromisoformat(checkin_date)
                    gap_nights = max((ci - co).days, 0)
                except Exception:
                    gap_nights = 0

            cleaning_payload = {
    "booking_id": raw_booking_id,  # ←ここに変更
    "property_name": property_name_normalized,
    "room_name": unit_normalized,
    "room_key": room_key,
    "task_date": checkout_date,
    "checkout_date": checkout_date,
    "next_checkin_date": checkin_date,
    "gap_nights": gap_nights,
    "guest_count": guest_count,
    "load_score": calc_load_score(guest_count, gap_nights),
    "status": "未着手",
    "note": "",
    "source": "beds24_csv",
}

            try:
                cleaning_res = (
    supabase.table("cleaning_tasks")
    .upsert(cleaning_payload, on_conflict="booking_id")  # ←ここ重要
    .execute()
)
                cleaning_saved.append({
                    "booking_id": raw_booking_id,
                    "cleaning_count": len(cleaning_res.data or []),
                })
            except Exception as e:
                skipped.append({
                    "reason": f"cleaning save error: {str(e)}",
                    "booking_id": raw_booking_id,
                })
                continue

        except Exception as e:
            skipped.append({
                "reason": f"row process error: {str(e)}",
                "row_index": i,
            })

    return {
        "from": start_date_str,
        "to": end_date_str,
        "csv_row_count": len(csv_rows) - 1,
        "raw_saved_count": len(raw_saved),
        "processed_saved_count": len(processed_saved),
        "cleaning_saved_count": len(cleaning_saved),
        "skipped_count": len(skipped),
        "raw_saved": raw_saved[:20],
        "processed_saved": processed_saved[:20],
        "cleaning_saved": cleaning_saved[:20],
        "skipped": skipped[:50],
    }
import re

def split_property_and_room(property_raw, unit_raw):
    property_raw = str(property_raw or "").strip()
    unit_raw = str(unit_raw or "").strip()

    # 物件マスタ
    property_list = [
        "FFFホテル","やなぎ橋","住吉","アクシオン美野島","ブランシェ","ウィングス","美野島",
        "玉井","ブランシェ","ウーブル博多","いそのビル","ジェン","ルッシェ","東光","グランデエス",
        "エスコート","アトラス","薬院","ロイズ","ピット","県庁前",
        "西中洲","冷泉","駅前モダン","比恵モダン","浄水"
    ]

    # propertyに部屋番号が付いているケース
    for p in property_list:
        if property_raw.startswith(p):
            rest = property_raw.replace(p,"").strip()

            # unitが空ならproperty側から取得
            if not unit_raw and rest:
                unit_raw = rest

            return p, unit_raw

    # fallback
    return property_raw, unit_raw
