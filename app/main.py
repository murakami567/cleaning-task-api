from datetime import date
from calendar import monthrange
from fastapi import FastAPI, Body, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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


@app.get("/")
def root():
    return {"status": "ok"}


# -----------------------------
# 清掃タスク
# -----------------------------
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
    assigned_staff_id: str | None = Body(None),
    assigned_staff_name: str | None = Body(None),
):
    payload = {}

    if status is not None:
        payload["status"] = status
    if note is not None:
        payload["note"] = note
    if assigned_staff_id is not None:
        payload["assigned_staff_id"] = assigned_staff_id
    if assigned_staff_name is not None:
        payload["assigned_staff_name"] = assigned_staff_name

    if not payload:
        raise HTTPException(status_code=400, detail="no update fields")

    res = (
        supabase.table("cleaning_tasks")
        .update(payload)
        .eq("id", task_id)
        .execute()
    )

    if not res.data:
        raise HTTPException(status_code=500, detail="task update failed")

    return res.data[0]


# -----------------------------
# 物件一覧 / 物件管理
# -----------------------------
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


# -----------------------------
# 部屋一覧 / 部屋管理
# -----------------------------
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


# -----------------------------
# 新規オープン進捗
# -----------------------------
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


# -----------------------------
# 設備管理
# -----------------------------
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


# -----------------------------
# スタッフ
# -----------------------------
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


# -----------------------------
# シフト管理
# -----------------------------
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


# -----------------------------
# 月間シフト表
# -----------------------------
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

import os
import requests
from datetime import datetime, timedelta

BEDS24_API_BASE = os.getenv("BEDS24_API_BASE", "https://beds24.com/api/v2")
BEDS24_TOKEN = os.getenv("BEDS24_TOKEN", "")


def beds24_headers():
    if not BEDS24_TOKEN:
        raise HTTPException(status_code=500, detail="BEDS24_TOKEN is not set")

    return {
        "accept": "application/json",
        "token": BEDS24_TOKEN,
    }


def normalize_property_name(name: str) -> str:
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
        if k in name:
            return v

    return name.strip()


def normalize_room_name(name: str) -> str:
    if not name:
        return ""
    return str(name).strip()


def calc_load_score(guest_count: int, gap_nights: int) -> int:
    score = guest_count or 0

    if gap_nights == 0:
        score += 2
    elif gap_nights == 1:
        score += 1

    return score


def extract_booking_fields(b: dict):
    booking_id = str(b.get("id") or b.get("bookingId") or "")

    property_name_raw = (
        b.get("propertyName")
        or b.get("property")
        or b.get("propName")
        or ""
    )

    room_name_raw = (
        b.get("roomName")
        or b.get("unitName")
        or b.get("room")
        or ""
    )

    checkin = (
        b.get("arrival")
        or b.get("checkIn")
        or b.get("firstNight")
    )

    checkout = (
        b.get("departure")
        or b.get("checkOut")
        or b.get("lastNight")
    )

    status_raw = str(b.get("status") or "")

    title_raw = str(
        b.get("title")
        or b.get("Title")
        or b.get("invoiceItemName")
        or ""
    )

    guest_count = int(b.get("numAdult", 0) or 0) + int(b.get("numChild", 0) or 0)

    return {
        "booking_id": booking_id,
        "property_name_raw": property_name_raw,
        "room_name_raw": room_name_raw,
        "checkin": checkin,
        "checkout": checkout,
        "status_raw": status_raw,
        "title_raw": title_raw,
        "guest_count": guest_count,
    }


@app.get("/beds24/bookings/test")
def beds24_bookings_test():
    url = f"{BEDS24_API_BASE}/bookings"
    params = {
        "from": (date.today() - timedelta(days=3)).isoformat(),
        "to": (date.today() + timedelta(days=30)).isoformat(),
    }

    res = requests.get(url, headers=beds24_headers(), params=params, timeout=30)

    if res.status_code != 200:
        raise HTTPException(status_code=res.status_code, detail=res.text)

    return res.json()


@app.post("/beds24/sync")
def beds24_sync_bookings(
    from_date: str | None = Body(None),
    to_date: str | None = Body(None),
):
    sync_from = from_date or (date.today() - timedelta(days=1)).isoformat()
    sync_to = to_date or (date.today() + timedelta(days=60)).isoformat()

    url = f"{BEDS24_API_BASE}/bookings"
    params = {
        "from": sync_from,
        "to": sync_to,
    }

    beds24_res = requests.get(url, headers=beds24_headers(), params=params, timeout=30)

    if beds24_res.status_code != 200:
        raise HTTPException(status_code=beds24_res.status_code, detail=beds24_res.text)

    bookings = beds24_res.json()

    raw_saved = []
    processed_saved = []
    cleaning_saved = []
    skipped = []

    for b in bookings:
        try:
            extracted = extract_booking_fields(b)

            booking_id = extracted["booking_id"]
            property_name_raw = extracted["property_name_raw"]
            room_name_raw = extracted["room_name_raw"]
            checkin = extracted["checkin"]
            checkout = extracted["checkout"]
            status_raw = extracted["status_raw"]
            title_raw = extracted["title_raw"]
            guest_count = extracted["guest_count"]

            # 1. 生データ保存
            raw_payload = {
                "booking_id": booking_id,
                "raw_json": b,
            }

            raw_res = supabase.table("beds24_raw_bookings").insert(raw_payload).execute()
            raw_saved.append({
                "booking_id": booking_id,
                "raw_count": len(raw_res.data or []),
            })

            if not property_name_raw or not room_name_raw or not checkout:
                skipped.append({
                    "reason": "missing required fields",
                    "booking_id": booking_id,
                })
                continue

            property_name_normalized = normalize_property_name(property_name_raw)
            room_name_normalized = normalize_room_name(room_name_raw)
            room_key = f"{property_name_normalized}{room_name_normalized}"

            status_lower = status_raw.lower()
            title_lower = title_raw.lower()

            is_cancelled = "cancel" in status_lower
            is_blocked = (
                "block" in title_lower
                or "blocked" in title_lower
                or "ブロック" in title_raw
            )

            # 2. 加工データ保存
            processed_payload = {
                "booking_id": booking_id,
                "property_name_raw": property_name_raw,
                "property_name_normalized": property_name_normalized,
                "room_name_raw": room_name_raw,
                "room_name_normalized": room_name_normalized,
                "room_key": room_key,
                "checkin_date": checkin[:10] if checkin else None,
                "checkout_date": checkout[:10] if checkout else None,
                "guest_count": guest_count,
                "status_raw": status_raw,
                "title_raw": title_raw,
                "is_cancelled": is_cancelled,
                "is_blocked": is_blocked,
            }

            processed_res = (
                supabase.table("beds24_processed_bookings")
                .upsert(processed_payload, on_conflict="booking_id")
                .execute()
            )

            processed_saved.append({
                "booking_id": booking_id,
                "processed_count": len(processed_res.data or []),
            })

            # キャンセル・ブロックは清掃タスクに入れない
            if is_cancelled:
                skipped.append({
                    "reason": "cancelled",
                    "booking_id": booking_id,
                })
                continue

            if is_blocked:
                skipped.append({
                    "reason": "blocked_by_title",
                    "booking_id": booking_id,
                    "title_raw": title_raw,
                })
                continue

            task_date = checkout[:10]
            checkout_date = checkout[:10]
            next_checkin_date = checkin[:10] if checkin else None

            gap_nights = 0
            if checkin and checkout:
                try:
                    co = datetime.fromisoformat(checkout[:10])
                    ci = datetime.fromisoformat(checkin[:10])
                    gap_nights = max((ci - co).days, 0)
                except Exception:
                    gap_nights = 0

            load_score = calc_load_score(guest_count, gap_nights)

            # 3. 清掃タスク保存
            cleaning_payload = {
                "reservation_id": booking_id,
                "property_name": property_name_normalized,
                "room_name": room_name_normalized,
                "room_key": room_key,
                "task_date": task_date,
                "checkout_date": checkout_date,
                "next_checkin_date": next_checkin_date,
                "gap_nights": gap_nights,
                "guest_count": guest_count,
                "load_score": load_score,
                "status": "未着手",
                "note": "",
                "source": "beds24",
            }

            cleaning_res = (
                supabase.table("cleaning_tasks")
                .upsert(cleaning_payload, on_conflict="reservation_id")
                .execute()
            )

            cleaning_saved.append({
                "booking_id": booking_id,
                "room_key": room_key,
                "task_date": task_date,
                "cleaning_count": len(cleaning_res.data or []),
            })

        except Exception as e:
            skipped.append({
                "reason": str(e),
                "booking_id": b.get("id") or b.get("bookingId"),
            })

    return {
        "from": sync_from,
        "to": sync_to,
        "raw_saved_count": len(raw_saved),
        "processed_saved_count": len(processed_saved),
        "cleaning_saved_count": len(cleaning_saved),
        "skipped_count": len(skipped),
        "raw_saved": raw_saved,
        "processed_saved": processed_saved,
        "cleaning_saved": cleaning_saved,
        "skipped": skipped,
    }
