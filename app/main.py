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
