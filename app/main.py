from datetime import date
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
# 今日のタスク取得
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


# -----------------------------
# 清掃タスク作成
# -----------------------------
@app.post("/tasks/create")
def create_task(
    property_name: str = Body(...),
    room_name: str = Body(...),
    room_key: str = Body(...),
    task_date: str = Body(...),
    status: str = Body("未着手"),
    note: str = Body("")
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
        "source": "manual"
    }

    res = supabase.table("cleaning_tasks").insert(payload).execute()

    if not res.data:
        raise HTTPException(status_code=500, detail="task creation failed")

    return res.data[0]


# -----------------------------
# 物件一覧
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


# -----------------------------
# 部屋一覧
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


# -----------------------------
# 物件追加
# -----------------------------
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


# -----------------------------
# 部屋追加
# -----------------------------
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
