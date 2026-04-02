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
    return res.data
