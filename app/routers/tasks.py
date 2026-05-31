from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, HTTPException

from app.db import supabase
from app.logger import get_logger

router = APIRouter(tags=["tasks"])
logger = get_logger(__name__)


def _auto_progress_started_tasks():
    """
    「清掃開始」状態で cleaning_started_at から 1 分経過したタスクを「清掃中」へ自動遷移させる。
    一覧取得系エンドポイントの先頭で呼ぶ。失敗しても取得処理自体は継続する。
    """
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        supabase.table("cleaning_tasks").update({"status": "清掃中"}).eq(
            "status", "清掃開始"
        ).lt("cleaning_started_at", cutoff).execute()
    except Exception as e:
        logger.error(f"auto_progress_started_tasks failed: {e}", exc_info=True)


# =========================================================
# 清掃タスク
# =========================================================
@router.get("/tasks/today")
def get_today_tasks():
    from datetime import date

    _auto_progress_started_tasks()

    today = date.today().isoformat()
    try:
        res = (
            supabase.table("cleaning_tasks")
            .select("*")
            .eq("task_date", today)
            .execute()
        )
    except Exception as e:
        logger.error(f"get_today_tasks failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="本日のタスク取得に失敗しました。")

    logger.info(f"get_today_tasks: date={today} count={len(res.data or [])}")
    return res.data


@router.get("/tasks/future")
def get_future_tasks():
    from datetime import date

    _auto_progress_started_tasks()

    today = date.today().isoformat()
    try:
        res = (
            supabase.table("cleaning_tasks")
            .select("*")
            .gt("task_date", today)
            .order("task_date")
            .execute()
        )
    except Exception as e:
        logger.error(f"get_future_tasks failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="将来のタスク取得に失敗しました。")

    logger.info(f"get_future_tasks: count={len(res.data or [])}")
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

    try:
        res = supabase.table("cleaning_tasks").insert(payload).execute()
    except Exception as e:
        logger.error(f"create_task failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="task creation failed")

    if not res.data:
        raise HTTPException(status_code=500, detail="task creation failed")

    logger.info(f"create_task: id={res.data[0].get('id')}")
    return res.data[0]


@router.post("/tasks/update")
def update_task(
    task_id: str = Body(...),
    task_date: str | None = Body(None),
    status: str | None = Body(None),
    note: str | None = Body(None),
    assigned_staff_ids: list[str] | None = Body(None),
    assigned_staff_names: list[str] | None = Body(None),
    assigned_staff_id: str | None = Body(None),
    assigned_staff_name: str | None = Body(None),
    checker_id: str | None = Body(None),
    checker_name: str | None = Body(None),
):
    payload = {}

    # 清掃日の更新
    # checkout_date / next_checkin_date は変更しない
    if task_date is not None:
        payload["task_date"] = task_date

    if status is not None:
        payload["status"] = status
        # 「清掃開始」になった瞬間にサーバ側で開始時刻を記録する。
        # それ以外のステータスに遷移したら開始時刻はクリアして残骸を残さない。
        if status == "清掃開始":
            payload["cleaning_started_at"] = datetime.now(timezone.utc).isoformat()
        else:
            payload["cleaning_started_at"] = None

    if note is not None:
        payload["note"] = note

    if assigned_staff_ids is not None:
        payload["assigned_staff_ids"] = assigned_staff_ids
        payload["assigned_staff_id"] = assigned_staff_ids[0] if len(assigned_staff_ids) > 0 else None

    if assigned_staff_names is not None:
        payload["assigned_staff_names"] = assigned_staff_names
        payload["assigned_staff_name"] = assigned_staff_names[0] if len(assigned_staff_names) > 0 else None

    if assigned_staff_id is not None:
        payload["assigned_staff_id"] = assigned_staff_id

    if assigned_staff_name is not None:
        payload["assigned_staff_name"] = assigned_staff_name

    if checker_id is not None:
        payload["checker_id"] = checker_id

    if checker_name is not None:
        payload["checker_name"] = checker_name

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
        logger.error(f"update_task failed: task_id={task_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"supabase update failed: {str(e)}")

    logger.info(f"update_task: task_id={task_id}")
    return {
        "ok": True,
        "task_id": task_id,
        "updated": payload,
        "data": res.data,
    }

# =========================================================
# 指定日タスク（過去も未来も対応）
# =========================================================
@router.get("/tasks/by-date")
def get_tasks_by_date(date: str):
    if not date:
        raise HTTPException(status_code=400, detail="date is required")

    _auto_progress_started_tasks()

    try:
        res = (
            supabase.table("cleaning_tasks")
            .select("*")
            .eq("task_date", date)
            .order("task_date")
            .execute()
        )
    except Exception as e:
        logger.error(f"get_tasks_by_date failed: date={date} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="タスク取得に失敗しました。")

    logger.info(f"get_tasks_by_date: date={date} count={len(res.data or [])}")
    return res.data

# =========================================================
# 清掃外タスク
# =========================================================
@router.get("/non-cleaning-tasks")
def get_non_cleaning_tasks():
    try:
        res = (
            supabase.table("non_cleaning_tasks")
            .select("*")
            .order("task_date")
            .execute()
        )
    except Exception as e:
        logger.error(f"get_non_cleaning_tasks failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="非清掃タスク取得に失敗しました。")

    logger.info(f"get_non_cleaning_tasks: count={len(res.data or [])}")
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

    try:
        res = supabase.table("non_cleaning_tasks").insert(payload).execute()
    except Exception as e:
        logger.error(f"create_non_cleaning_task failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="non cleaning task creation failed")

    if not res.data:
        raise HTTPException(status_code=500, detail="non cleaning task creation failed")

    logger.info(f"create_non_cleaning_task: id={res.data[0].get('id')}")
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

    try:
        res = (
            supabase.table("non_cleaning_tasks")
            .update(payload)
            .eq("id", task_id)
            .execute()
        )
    except Exception as e:
        logger.error(f"update_non_cleaning_task failed: task_id={task_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="non cleaning task update failed")

    if not res.data:
        raise HTTPException(status_code=500, detail="non cleaning task update failed")

    logger.info(f"update_non_cleaning_task: task_id={task_id}")
    return res.data[0]


@router.post("/non-cleaning-tasks/delete")
def delete_non_cleaning_task(task_id: str = Body(...)):
    try:
        res = (
            supabase.table("non_cleaning_tasks")
            .delete()
            .eq("id", task_id)
            .execute()
        )
    except Exception as e:
        logger.error(f"delete_non_cleaning_task failed: task_id={task_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="非清掃タスクの削除に失敗しました。")

    logger.info(f"delete_non_cleaning_task: task_id={task_id}")
    return {"ok": True, "data": res.data}


# =========================================================
# 物件 / 部屋
# =========================================================
@router.get("/properties")
def get_properties():
    try:
        res = (
            supabase.table("properties")
            .select("*")
            .order("sort_order")
            .order("property_name")
            .execute()
        )
    except Exception as e:
        logger.error(f"get_properties failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="物件情報の取得に失敗しました。")

    logger.info(f"get_properties: count={len(res.data or [])}")
    return res.data or []


@router.post("/properties/create")
def create_property(
    property_code: str = Body(...),
    property_name: str = Body(...),
    normalized_name: str | None = Body(None),
    sort_order: int = Body(999),
    is_active: bool = Body(True),
):
    payload = {
        "property_code": property_code.strip(),
        "property_name": property_name.strip(),
        "normalized_name": (normalized_name or property_name).strip(),
        "sort_order": sort_order,
        "is_active": is_active,
    }

    try:
        res = (
            supabase.table("properties")
            .insert(payload)
            .execute()
        )
    except Exception as e:
        logger.error(f"create_property failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="property create failed")

    if not res.data:
        raise HTTPException(status_code=500, detail="property create failed")

    logger.info(f"create_property: id={res.data[0].get('id')}")
    return res.data[0]


@router.post("/properties/update")
def update_property(
    property_id: str = Body(...),
    property_code: str | None = Body(None),
    property_name: str | None = Body(None),
    normalized_name: str | None = Body(None),
    sort_order: int | None = Body(None),
    is_active: bool | None = Body(None),
):
    payload = {}

    if property_code is not None:
        payload["property_code"] = property_code.strip()
    if property_name is not None:
        payload["property_name"] = property_name.strip()
    if normalized_name is not None:
        payload["normalized_name"] = normalized_name.strip()
    if sort_order is not None:
        payload["sort_order"] = sort_order
    if is_active is not None:
        payload["is_active"] = is_active

    if not payload:
        raise HTTPException(status_code=400, detail="no update fields")

    try:
        res = (
            supabase.table("properties")
            .update(payload)
            .eq("id", property_id)
            .execute()
        )
    except Exception as e:
        logger.error(f"update_property failed: property_id={property_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="property update failed")

    if not res.data:
        raise HTTPException(status_code=500, detail="property update failed")

    logger.info(f"update_property: property_id={property_id}")
    return res.data[0]


@router.get("/rooms")
def get_rooms(property_id: str | None = None):
    try:
        query = (
            supabase.table("rooms")
            .select("*")
            .order("room_sort_order")
            .order("room_name")
        )

        if property_id:
            query = query.eq("property_id", property_id)

        res = query.execute()
    except Exception as e:
        logger.error(f"get_rooms failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="部屋情報の取得に失敗しました。")

    logger.info(f"get_rooms: count={len(res.data or [])}")
    return res.data or []


@router.post("/rooms/create")
def create_room(
    property_id: str = Body(...),
    room_name: str = Body(...),
    room_code: str | None = Body(None),
    room_key: str = Body(...),
    normalized_room_key: str | None = Body(None),
    capacity: int = Body(1),
    room_sort_order: int = Body(999),
    is_active: bool = Body(True),
    prep_d: int = Body(0),
    prep_s: int = Body(0),
    prep_spare_s: int = Body(0),
    prep_ta: int = Body(0),
):
    payload = {
        "property_id": property_id,
        "room_name": room_name.strip(),
        "room_code": (room_code or room_name).strip(),
        "room_key": room_key.strip(),
        "normalized_room_key": (normalized_room_key or room_key).strip(),
        "capacity": capacity,
        "room_sort_order": room_sort_order,
        "is_active": is_active,
        "prep_d": prep_d,
        "prep_s": prep_s,
        "prep_spare_s": prep_spare_s,
        "prep_ta": prep_ta,
    }

    try:
        res = (
            supabase.table("rooms")
            .insert(payload)
            .execute()
        )
    except Exception as e:
        logger.error(f"create_room failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="room create failed")

    if not res.data:
        raise HTTPException(status_code=500, detail="room create failed")

    logger.info(f"create_room: id={res.data[0].get('id')}")
    return res.data[0]


@router.post("/rooms/update")
def update_room(
    room_id: str = Body(...),
    property_id: str | None = Body(None),
    room_name: str | None = Body(None),
    room_code: str | None = Body(None),
    room_key: str | None = Body(None),
    normalized_room_key: str | None = Body(None),
    capacity: int | None = Body(None),
    room_sort_order: int | None = Body(None),
    is_active: bool | None = Body(None),
    prep_d: int | None = Body(None),
    prep_s: int | None = Body(None),
    prep_spare_s: int | None = Body(None),
    prep_ta: int | None = Body(None),
):
    payload = {}

    if property_id is not None:
        payload["property_id"] = property_id
    if room_name is not None:
        payload["room_name"] = room_name.strip()
    if room_code is not None:
        payload["room_code"] = room_code.strip()
    if room_key is not None:
        payload["room_key"] = room_key.strip()
    if normalized_room_key is not None:
        payload["normalized_room_key"] = normalized_room_key.strip()
    if capacity is not None:
        payload["capacity"] = capacity
    if room_sort_order is not None:
        payload["room_sort_order"] = room_sort_order
    if is_active is not None:
        payload["is_active"] = is_active
    if prep_d is not None:
        payload["prep_d"] = prep_d
    if prep_s is not None:
        payload["prep_s"] = prep_s
    if prep_spare_s is not None:
        payload["prep_spare_s"] = prep_spare_s
    if prep_ta is not None:
        payload["prep_ta"] = prep_ta

    if not payload:
        raise HTTPException(status_code=400, detail="no update fields")

    try:
        res = (
            supabase.table("rooms")
            .update(payload)
            .eq("id", room_id)
            .execute()
        )
    except Exception as e:
        logger.error(f"update_room failed: room_id={room_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="room update failed")

    if not res.data:
        raise HTTPException(status_code=500, detail="room update failed")

    logger.info(f"update_room: room_id={room_id}")
    return res.data[0]


@router.post("/rooms/bulk-create")
def bulk_create_rooms(
    property_id: str = Body(...),
    room_names: list[str] = Body(...),
    default_capacity: int = Body(1),
    start_sort_order: int = Body(1),
):
    prop_res = (
        supabase.table("properties")
        .select("*")
        .eq("id", property_id)
        .limit(1)
        .execute()
    )

    if not prop_res.data:
        raise HTTPException(status_code=404, detail="property not found")

    property_row = prop_res.data[0]
    property_name = property_row.get("property_name") or ""

    cleaned_names = []
    for name in room_names:
        n = (name or "").strip()
        if n:
            cleaned_names.append(n)

    if not cleaned_names:
        raise HTTPException(status_code=400, detail="room_names is empty")

    payloads = []
    current_sort = start_sort_order

    for room_name in cleaned_names:
        room_code = room_name
        room_key = f"{property_name}{room_name}"

        payloads.append({
            "property_id": property_id,
            "room_name": room_name,
            "room_code": room_code,
            "room_key": room_key,
            "normalized_room_key": room_key,
            "capacity": default_capacity,
            "room_sort_order": current_sort,
            "is_active": True,
        })
        current_sort += 1

    try:
        res = (
            supabase.table("rooms")
            .insert(payloads)
            .execute()
        )
    except Exception as e:
        logger.error(f"bulk_create_rooms failed: property_id={property_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="bulk room create failed")

    if not res.data:
        raise HTTPException(status_code=500, detail="bulk room create failed")

    logger.info(f"bulk_create_rooms: property_id={property_id} count={len(res.data)}")
    return {
        "ok": True,
        "count": len(res.data),
        "data": res.data,
    }


@router.post("/rooms/delete")
def delete_room(payload: dict = Body(...)):
    room_id = payload.get("room_id")

    if not room_id:
        raise HTTPException(status_code=400, detail="room_id is required")

    try:
        res = (
            supabase.table("rooms")
            .delete()
            .eq("id", room_id)
            .execute()
        )
    except Exception as e:
        logger.error(f"delete_room failed: room_id={room_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="部屋の削除に失敗しました。")

    logger.info(f"delete_room: room_id={room_id}")
    return {"ok": True, "data": res.data}

# =========================================================
# シフト管理
# =========================================================

@router.get("/shifts")
def get_shifts(shift_date: str | None = None):
    try:
        query = (
            supabase.table("shift_days")
            .select("*, shift_entries(*, staff_members(*))")
            .order("shift_date")
        )

        if shift_date:
            query = query.eq("shift_date", shift_date)

        res = query.execute()
    except Exception as e:
        logger.error(f"get_shifts failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="シフト情報の取得に失敗しました。")

    logger.info(f"get_shifts: count={len(res.data or [])}")
    return res.data or []

@router.get("/staffs")
def get_staffs():
    try:
        res = (
            supabase.table("staff_members")
            .select("*")
            .order("sort_order")
            .order("staff_name")
            .execute()
        )
    except Exception as e:
        logger.error(f"get_staffs failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="スタッフ情報の取得に失敗しました。")

    logger.info(f"get_staffs: count={len(res.data or [])}")
    return res.data or []

# =========================================================
# スタッフ保存
# =========================================================

@router.post("/staffs/upsert")
def upsert_staff(
    staff_id: str | None = Body(None),
    staff_code: str = Body(...),
    staff_name: str = Body(...),
    role: str = Body("staff"),
    sort_order: int = Body(999),
    is_active: bool = Body(True),
    note: str = Body(""),
    password: str | None = Body(None),
):

    payload = {
        "staff_code": staff_code,
        "staff_name": staff_name,
        "role": role,
        "sort_order": sort_order,
        "is_active": is_active,
        "note": note
    }

    if password is not None:
        payload["password"] = password

    # 更新
    try:
        if staff_id:
            res = (
                supabase.table("staff_members")
                .update(payload)
                .eq("id", staff_id)
                .execute()
            )
        else:
            res = (
                supabase.table("staff_members")
                .insert(payload)
                .execute()
            )
    except Exception as e:
        logger.error(f"upsert_staff failed: staff_id={staff_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="staff save failed")

    if not res.data:
        raise HTTPException(status_code=500, detail="staff save failed")

    logger.info(f"upsert_staff: staff_id={res.data[0].get('id')}")
    return res.data[0]

@router.get("/shift-board")
def get_shift_board(year: int, month: int):
    from datetime import date, timedelta
    from collections import defaultdict

    try:
        month_start = date(year, month, 1)

        if month == 12:
            month_end = date(year + 1, 1, 1)
        else:
            month_end = date(year, month + 1, 1)

        # 週表示で月跨ぎしても件数が欠けないように前後7日分広げる
        range_start = month_start - timedelta(days=7)
        range_end = month_end + timedelta(days=7)

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
            .gte("shift_date", range_start.isoformat())
            .lt("shift_date", range_end.isoformat())
            .order("shift_date")
            .execute()
        )

        task_res = (
            supabase.table("cleaning_tasks")
            .select("task_date")
            .gte("task_date", range_start.isoformat())
            .lt("task_date", range_end.isoformat())
            .execute()
        )
    except Exception as e:
        logger.error(f"get_shift_board failed: year={year} month={month} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="シフトボードの取得に失敗しました。")

    cleaning_counts = defaultdict(int)
    for row in task_res.data or []:
        task_date = row.get("task_date")
        if task_date:
            cleaning_counts[task_date] += 1

    attendance_counts = defaultdict(int)
    for day in day_res.data or []:
        shift_date = day.get("shift_date")
        entries = day.get("shift_entries") or []
        count = 0

        for entry in entries:
            status = entry.get("status")
            if status in ["出勤", "遅刻"]:
                count += 1

        if shift_date:
            attendance_counts[shift_date] = count

    workload = {}
    all_dates = set(cleaning_counts.keys()) | set(attendance_counts.keys())

    for d in all_dates:
        clean = cleaning_counts.get(d, 0)
        attendance = attendance_counts.get(d, 0)
        workload[d] = round(clean / attendance, 1) if attendance > 0 else 0

    logger.info(f"get_shift_board: year={year} month={month}")
    return {
        "staffs": staff_res.data or [],
        "days": day_res.data or [],
        "cleaning_counts": dict(cleaning_counts),
        "attendance_counts": dict(attendance_counts),
        "workload": workload,
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
    try:
        res = (
            supabase.table("opening_projects")
            .select("*")
            .order("due_date")
            .execute()
        )
    except Exception as e:
        logger.error(f"get_openings failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="オープン進捗の取得に失敗しました。")

    logger.info(f"get_openings: count={len(res.data or [])}")
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

    try:
        res = supabase.table("opening_projects").insert(payload).execute()
    except Exception as e:
        logger.error(f"create_opening failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="opening creation failed")

    if not res.data:
        raise HTTPException(status_code=500, detail="opening creation failed")

    logger.info(f"create_opening: id={res.data[0].get('id')}")
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

    try:
        res = (
            supabase.table("opening_projects")
            .update(payload)
            .eq("id", opening_id)
            .execute()
        )
    except Exception as e:
        logger.error(f"update_opening failed: opening_id={opening_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="opening update failed")

    if not res.data:
        raise HTTPException(status_code=500, detail="opening update failed")

    logger.info(f"update_opening: opening_id={opening_id}")
    return res.data[0]


@router.post("/openings/delete")
def delete_opening(opening_id: str = Body(...)):
    try:
        res = (
            supabase.table("opening_projects")
            .delete()
            .eq("id", opening_id)
            .execute()
        )
    except Exception as e:
        logger.error(f"delete_opening failed: opening_id={opening_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="オープン進捗の削除に失敗しました。")

    logger.info(f"delete_opening: opening_id={opening_id}")
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
        logger.info(f"get_facilities: count={len(res.data or [])}")
        return res.data or []
    except Exception as e:
        logger.error(f"get_facilities failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"facilities fetch failed: {str(e)}")


@router.post("/facilities/create")
def create_facility(payload: dict = Body(...)):
    try:
        res = supabase.table("facility_tasks").insert(payload).execute()
        if not res.data:
            raise HTTPException(status_code=500, detail="facility create failed")
        logger.info(f"create_facility: id={res.data[0].get('id')}")
        return res.data[0]
    except Exception as e:
        logger.error(f"create_facility failed: {e}", exc_info=True)
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
        logger.info(f"update_facility: facility_id={facility_id}")
        return res.data[0]
    except Exception as e:
        logger.error(f"update_facility failed: facility_id={facility_id} {e}", exc_info=True)
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
        logger.info(f"delete_facility: facility_id={facility_id}")
        return {"ok": True, "data": res.data}
    except Exception as e:
        logger.error(f"delete_facility failed: facility_id={facility_id} {e}", exc_info=True)
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

    # ===============================
    # 清掃タスク
    # ===============================
    cleaning_res = (
        supabase.table("cleaning_tasks")
        .select("*")
        .contains("assigned_staff_ids", [staff_id])
        .order("task_date")
        .execute()
    )

    # ===============================
    # チェックタスク
    # ===============================
    check_res = (
    supabase.table("cleaning_tasks")
    .select("*")
    .eq("checker_name", staff_name)
    .order("task_date")
    .execute()
)

    # ===============================
    # その他タスク
    # ===============================
    other_res = (
        supabase.table("non_cleaning_tasks")
        .select("*")
        .contains("assignee_ids", [staff_id])
        .order("task_date")
        .execute()
    )

    cleaning_rows = cleaning_res.data or []
    check_rows = check_res.data or []
    other_rows = other_res.data or []

    # ===============================
    # タオル計算
    # ===============================
    def calc_towel_count(property_name, next_guest_count, next_stay_nights):

        if property_name in ["FFFホテル", "やなぎ橋"]:
            return ""

        guests = int(next_guest_count or 0)
        nights = int(next_stay_nights or 0)

        if guests <= 0 or nights <= 0:
            return ""

        if nights >= 8:
            return guests * 3
        elif nights >= 3:
            return guests * 2
        else:
            return guests

    cleaning_tasks = []
    for row in cleaning_rows:

        cleaning_tasks.append({
            "id": row.get("id"),
            "type": "cleaning",
            "propertyName": row.get("property_name"),
            "roomName": row.get("room_name"),
            "date": row.get("task_date"),
            "deadline": row.get("next_checkin_date"),
            "status": row.get("status"),
            "note": row.get("note"),
            "assigneeName": (
                row.get("assigned_staff_names", [""])[0]
                if isinstance(row.get("assigned_staff_names"), list) and row.get("assigned_staff_names")
                else row.get("assigned_staff_name")
            ),
            "checkerName": row.get("checker_name"),
            "towelCount": calc_towel_count(
                row.get("property_name"),
                row.get("next_guest_count"),
                row.get("next_stay_nights")
            )
        })

    # ===============================
    # チェックタスク
    # ===============================
    check_tasks = []
    for row in check_rows:

        check_tasks.append({
            "id": row.get("id"),
            "type": "check",
            "propertyName": row.get("property_name"),
            "roomName": row.get("room_name"),
            "date": row.get("task_date"),
            "deadline": row.get("next_checkin_date"),
            "status": row.get("status"),
            "checkerName": row.get("checker_name"),
            "note": row.get("note"),
        })

    # ===============================
    # その他タスク
    # ===============================
    other_tasks = []
    for row in other_rows:

        other_tasks.append({
            "id": row.get("id"),
            "type": "other",
            "title": row.get("title"),
            "date": row.get("task_date"),
            "deadline": row.get("deadline"),
            "status": row.get("status"),
            "assigneeName": (
                row.get("assignee_names", [""])[0]
                if isinstance(row.get("assignee_names"), list) and row.get("assignee_names")
                else row.get("assignee_name")
            ),
            "checkerName": row.get("checker_name"),
            "note": row.get("note"),
        })

    return {
        "cleaningTasks": cleaning_tasks,
        "checkTasks": check_tasks,
        "otherTasks": other_tasks
    }

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
