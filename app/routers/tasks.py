from fastapi import APIRouter, Body, HTTPException

from app.db import supabase

router = APIRouter(tags=["tasks"])


# =========================================================
# 清掃タスク
# =========================================================
@router.get("/tasks/today")
def get_today_tasks():
    from datetime import date

    today = date.today().isoformat()

    res = (
        supabase.table("cleaning_tasks")
        .select("*")
        .eq("task_date", today)
        .execute()
    )

    return res.data


@router.get("/tasks/future")
def get_future_tasks():
    from datetime import date

    today = date.today().isoformat()

    res = (
        supabase.table("cleaning_tasks")
        .select("*")
        .gt("task_date", today)
        .order("task_date")
        .execute()
    )

    return res.data


@router.post("/tasks/create")
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


@router.post("/tasks/update")
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

    # 外部キー問題があるなら単数列は無理に触らない
    if assigned_staff_id is not None:
        payload["assigned_staff_id"] = assigned_staff_id

    if assigned_staff_name is not None:
        payload["assigned_staff_name"] = assigned_staff_name

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
# 清掃外タスク
# =========================================================
@router.get("/non-cleaning-tasks")
def get_non_cleaning_tasks():
    res = (
        supabase.table("non_cleaning_tasks")
        .select("*")
        .order("task_date")
        .execute()
    )
    return res.data


@router.post("/non-cleaning-tasks/create")
def create_non_cleaning_task(
    task_date: str = Body(...),
    status: str = Body("未着手"),
    category: str = Body("OTHER"),
    title: str = Body(...),
    deadline: str | None = Body(None),
    assignee_ids: list[str] | None = Body(None),
    assignee_names: list[str] | None = Body(None),
    checker_id: str | None = Body(None),
    checker_name: str | None = Body(None),
    note: str = Body(""),
):
    payload = {
        "task_date": task_date,
        "status": status,
        "category": category,
        "title": title,
        "deadline": deadline,
        "assignee_ids": assignee_ids or [],
        "assignee_names": assignee_names or [],
        "checker_id": checker_id,
        "checker_name": checker_name,
        "note": note,
    }

    # 旧単数列も残すなら先頭だけ入れる
    payload["assignee_id"] = assignee_ids[0] if assignee_ids and len(assignee_ids) > 0 else None
    payload["assignee_name"] = assignee_names[0] if assignee_names and len(assignee_names) > 0 else None

    res = supabase.table("non_cleaning_tasks").insert(payload).execute()

    if not res.data:
        raise HTTPException(status_code=500, detail="non cleaning task creation failed")

    return res.data[0]


@router.post("/non-cleaning-tasks/update")
def update_non_cleaning_task(
    task_id: str = Body(...),
    task_date: str | None = Body(None),
    status: str | None = Body(None),
    category: str | None = Body(None),
    title: str | None = Body(None),
    deadline: str | None = Body(None),
    assignee_ids: list[str] | None = Body(None),
    assignee_names: list[str] | None = Body(None),
    checker_id: str | None = Body(None),
    checker_name: str | None = Body(None),
    note: str | None = Body(None),
):
    payload = {}

    if task_date is not None:
        payload["task_date"] = task_date
    if status is not None:
        payload["status"] = status
    if category is not None:
        payload["category"] = category
    if title is not None:
        payload["title"] = title
    if deadline is not None:
        payload["deadline"] = deadline
    if assignee_ids is not None:
        payload["assignee_ids"] = assignee_ids
        payload["assignee_id"] = assignee_ids[0] if len(assignee_ids) > 0 else None
    if assignee_names is not None:
        payload["assignee_names"] = assignee_names
        payload["assignee_name"] = assignee_names[0] if len(assignee_names) > 0 else None
    if checker_id is not None:
        payload["checker_id"] = checker_id
    if checker_name is not None:
        payload["checker_name"] = checker_name
    if note is not None:
        payload["note"] = note

    if not payload:
        raise HTTPException(status_code=400, detail="no update fields")

    res = (
        supabase.table("non_cleaning_tasks")
        .update(payload)
        .eq("id", task_id)
        .execute()
    )

    if not res.data:
        raise HTTPException(status_code=500, detail="non cleaning task update failed")

    return res.data[0]


@router.post("/non-cleaning-tasks/delete")
def delete_non_cleaning_task(task_id: str = Body(...)):
    res = (
        supabase.table("non_cleaning_tasks")
        .delete()
        .eq("id", task_id)
        .execute()
    )
    return {"ok": True, "data": res.data}


# =========================================================
# 物件 / 部屋 / シフト
# =========================================================
@router.get("/properties")
def get_properties():
    res = (
        supabase.table("properties")
        .select("*")
        .order("sort_order")
        .order("property_name")
        .execute()
    )
    return res.data


@router.get("/rooms")
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


# =========================================================
# シフト管理
# =========================================================

@router.get("/shifts")
def get_shifts(shift_date: str | None = None):
    query = (
        supabase.table("shift_days")
        .select("*, shift_entries(*, staff_members(*))")
        .order("shift_date")
    )

    if shift_date:
        query = query.eq("shift_date", shift_date)

    res = query.execute()
    return res.data or []

@router.get("/staffs")
def get_staffs():
    res = (
        supabase.table("staff_members")
        .select("*")
        .order("sort_order")
        .order("staff_name")
        .execute()
    )
    return res.data or []


@router.get("/shift-board")
def get_shift_board(year: int, month: int):
    from datetime import date

    start_date = date(year, month, 1).isoformat()

    if month == 12:
        end_date = date(year + 1, 1, 1).isoformat()
    else:
        end_date = date(year, month + 1, 1).isoformat()

    staff_res = (
        supabase.table("staff_members")
        .select("*")
        .order("sort_order")
        .order("staff_name")
        .execute()
    )

    day_res = (
        supabase.table("shift_days")
        .select("*, shift_entries(*, staff_members(*))")
        .gte("shift_date", start_date)
        .lt("shift_date", end_date)
        .order("shift_date")
        .execute()
    )

    return {
        "staffs": staff_res.data or [],
        "days": day_res.data or [],
    }


@router.post("/shifts/create_day")
def create_shift_day(
    shift_date: str = Body(...),
    note: str | None = Body(None),
):
    # 既存確認
    existing = (
        supabase.table("shift_days")
        .select("*")
        .eq("shift_date", shift_date)
        .execute()
    )

    if existing.data and len(existing.data) > 0:
        day = existing.data[0]
        day["shift_entries"] = day.get("shift_entries", []) if isinstance(day.get("shift_entries"), list) else []
        return day

    payload = {
        "shift_date": shift_date,
        "note": note or "",
    }

    res = (
        supabase.table("shift_days")
        .insert(payload)
        .execute()
    )

    if not res.data:
        raise HTTPException(status_code=500, detail="shift day creation failed")

    created = res.data[0]
    created["shift_entries"] = []
    return created


@router.post("/shifts/get_or_create_day")
def get_or_create_shift_day(
    shift_date: str = Body(...),
    note: str | None = Body(None),
):
    existing = (
        supabase.table("shift_days")
        .select("*, shift_entries(*, staff_members(*))")
        .eq("shift_date", shift_date)
        .execute()
    )

    if existing.data and len(existing.data) > 0:
        day = existing.data[0]
        day["shift_entries"] = day.get("shift_entries", []) if isinstance(day.get("shift_entries"), list) else []
        return day

    payload = {
        "shift_date": shift_date,
        "note": note or "",
    }

    created_res = (
        supabase.table("shift_days")
        .insert(payload)
        .execute()
    )

    if not created_res.data:
        raise HTTPException(status_code=500, detail="get_or_create_day failed")

    created = created_res.data[0]
    created["shift_entries"] = []
    return created


@router.post("/shifts/upsert_entry")
def upsert_shift_entry(
    shift_day_id: str = Body(...),
    staff_id: str = Body(...),
    status: str = Body(...),
    start_time: str | None = Body(None),
    end_time: str | None = Body(None),
    assigned_area: str | None = Body(None),
    note: str | None = Body(None),
):
    # 既存確認
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
        "assigned_area": assigned_area or "",
        "note": note or "",
    }

    if existing.data and len(existing.data) > 0:
        entry_id = existing.data[0]["id"]
        res = (
            supabase.table("shift_entries")
            .update(payload)
            .eq("id", entry_id)
            .execute()
        )
    else:
        res = (
            supabase.table("shift_entries")
            .insert(payload)
            .execute()
        )

    if not res.data:
        raise HTTPException(status_code=500, detail="shift entry upsert failed")

    return res.data[0]

# =========================================================
# 新規オープン進捗
# =========================================================
@router.get("/openings")
def get_openings():
    res = (
        supabase.table("opening_projects")
        .select("*")
        .order("due_date")
        .execute()
    )
    return res.data or []


@router.post("/openings/create")
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


@router.post("/openings/update")
def update_opening(
    opening_id: str = Body(...),
    property_id: str | None = Body(None),
    property_name: str | None = Body(None),
    room_name: str | None = Body(None),
    title: str | None = Body(None),
    owner_name: str | None = Body(None),
    due_date: str | None = Body(None),
    status: str | None = Body(None),
    priority: str | None = Body(None),
    progress: int | None = Body(None),
    memo: str | None = Body(None),
):
    payload = {}

    if property_id is not None:
        payload["property_id"] = property_id
    if property_name is not None:
        payload["property_name"] = property_name
    if room_name is not None:
        payload["room_name"] = room_name
    if title is not None:
        payload["title"] = title
    if owner_name is not None:
        payload["owner_name"] = owner_name
    if due_date is not None:
        payload["due_date"] = due_date
    if status is not None:
        payload["status"] = status
    if priority is not None:
        payload["priority"] = priority
    if progress is not None:
        payload["progress"] = progress
    if memo is not None:
        payload["memo"] = memo

    if not payload:
        raise HTTPException(status_code=400, detail="no update fields")

    res = (
        supabase.table("opening_projects")
        .update(payload)
        .eq("id", opening_id)
        .execute()
    )

    if not res.data:
        raise HTTPException(status_code=500, detail="opening update failed")

    return res.data[0]


@router.post("/openings/delete")
def delete_opening(opening_id: str = Body(...)):
    res = (
        supabase.table("opening_projects")
        .delete()
        .eq("id", opening_id)
        .execute()
    )
    return {"ok": True, "data": res.data}

# =========================================================
# 設備管理
# =========================================================
@router.get("/facilities")
def get_facilities():
    try:
        res = (
            supabase.table("facility_tasks")
            .select("*")
            .order("created_at")
            .execute()
        )
        return res.data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"facilities fetch failed: {str(e)}")


@router.post("/facilities/create")
def create_facility(payload: dict = Body(...)):
    try:
        res = supabase.table("facility_tasks").insert(payload).execute()
        if not res.data:
            raise HTTPException(status_code=500, detail="facility create failed")
        return res.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"facility create failed: {str(e)}")


@router.post("/facilities/update")
def update_facility(
    facility_id: str = Body(...),
    property_id: str | None = Body(None),
    property_name: str | None = Body(None),
    room_name: str | None = Body(None),
    assignee: str | None = Body(None),
    content: str | None = Body(None),
    start_date: str | None = Body(None),
    end_date: str | None = Body(None),
    status: str | None = Body(None),
    note: str | None = Body(None),
):
    payload = {}

    if property_id is not None:
        payload["property_id"] = property_id
    if property_name is not None:
        payload["property_name"] = property_name
    if room_name is not None:
        payload["room_name"] = room_name
    if assignee is not None:
        payload["assignee"] = assignee
    if content is not None:
        payload["content"] = content
    if start_date is not None:
        payload["start_date"] = start_date
    if end_date is not None:
        payload["end_date"] = end_date
    if status is not None:
        payload["status"] = status
    if note is not None:
        payload["note"] = note

    if not payload:
        raise HTTPException(status_code=400, detail="no update fields")

    try:
        res = (
            supabase.table("facility_tasks")
            .update(payload)
            .eq("id", facility_id)
            .execute()
        )
        if not res.data:
            raise HTTPException(status_code=500, detail="facility update failed")
        return res.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"facility update failed: {str(e)}")


@router.post("/facilities/delete")
def delete_facility(facility_id: str = Body(...)):
    try:
        res = (
            supabase.table("facility_tasks")
            .delete()
            .eq("id", facility_id)
            .execute()
        )
        return {"ok": True, "data": res.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"facility delete failed: {str(e)}")

# =========================================================
# 社員アプリ
# =========================================================

@router.get("/employee/home")
def employee_home(staff_id: str):
    today_tasks = (
        supabase.table("cleaning_tasks")
        .select("*")
        .contains("assigned_staff_ids", [staff_id])
        .execute()
    )

    return {
        "todayTaskCount": len(today_tasks.data or []),
        "upcomingTaskCount": 0,
        "todayScheduleCount": 0,
        "unreadNoticeCount": 0
    }


@router.get("/employee/tasks")
def employee_tasks(staff_id: str):

    res = (
        supabase.table("cleaning_tasks")
        .select("*")
        .contains("assigned_staff_ids", [staff_id])
        .order("task_date")
        .execute()
    )

    return res.data or []


@router.get("/employee/schedule")
def employee_schedule(staff_id: str):

    res = (
        supabase.table("shift_entries")
        .select("*, shift_days(*)")
        .eq("staff_id", staff_id)
        .execute()
    )

    return res.data or []


@router.post("/employee/worklog")
def employee_worklog(payload: dict = Body(...)):

    res = supabase.table("worklogs").insert(payload).execute()

    if not res.data:
        raise HTTPException(status_code=500, detail="worklog insert failed")

    return res.data[0]
