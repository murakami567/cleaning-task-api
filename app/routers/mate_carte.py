from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.db import supabase
from app.logger import get_logger
from app.services.auth_service import require_admin_or_leader

router = APIRouter(prefix="/mate-cartes", tags=["mate-cartes"])
logger = get_logger(__name__)


def _get_current_staff(current_user: dict) -> dict[str, Any]:
    user_id = current_user.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="認証情報が不正です。")
    try:
        res = (
            supabase.table("staff_members")
            .select("id, staff_name, staff_code, role")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )
    except Exception as e:
        logger.error(f"mate current staff lookup failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="ログインスタッフ取得に失敗しました。")
    if not res.data:
        raise HTTPException(status_code=401, detail="ログインスタッフが見つかりません。")
    return res.data[0]


def _month_range(base: date) -> tuple[str, str]:
    start = base.replace(day=1)
    if start.month == 12:
        next_month = start.replace(year=start.year + 1, month=1)
    else:
        next_month = start.replace(month=start.month + 1)
    end = next_month - timedelta(days=1)
    return start.isoformat(), end.isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _staff_matches_task(row: dict[str, Any], staff_id: str) -> bool:
    ids = row.get("assigned_staff_ids") if isinstance(row.get("assigned_staff_ids"), list) else []
    return staff_id in [str(x) for x in ids] or str(row.get("assigned_staff_id") or "") == staff_id


def _task_stats(rows: list[dict[str, Any]], staff_id: str) -> dict[str, Any]:
    matched: list[dict[str, Any]] = []
    durations: list[int] = []
    properties: list[str] = []

    for row in rows:
        if not _staff_matches_task(row, staff_id):
            continue
        completed = row.get("cleaning_completed_at")
        if not completed and row.get("status") not in ["完了", "チェック完了"]:
            continue
        matched.append(row)
        property_name = str(row.get("property_name") or "").strip()
        if property_name and property_name not in properties:
            properties.append(property_name)
        start_dt = _parse_dt(row.get("cleaning_started_at"))
        end_dt = _parse_dt(completed)
        if start_dt and end_dt and end_dt >= start_dt:
            durations.append(round((end_dt - start_dt).total_seconds() / 60))

    return {
        "cleaning_count": len(matched),
        "average_cleaning_minutes": round(sum(durations) / len(durations)) if durations else None,
        "worked_property_names": properties,
    }


def _pick_staff_value(staff: dict[str, Any], keys: list[str]):
    for key in keys:
        value = staff.get(key)
        if value is not None and value != "":
            return value
    return None


@router.get("/targets")
def get_mate_targets(current_user: dict = Depends(require_admin_or_leader)):
    try:
        res = (
            supabase.table("staff_members")
            .select("id, staff_code, staff_name, role, is_active, sort_order")
            .in_("role", ["staff", "checker"])
            .eq("is_active", True)
            .order("sort_order")
            .order("staff_name")
            .execute()
        )
    except Exception as e:
        logger.error(f"get_mate_targets failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="対象スタッフ取得に失敗しました。")
    return res.data or []


@router.get("/properties")
def get_mate_properties(current_user: dict = Depends(require_admin_or_leader)):
    try:
        res = (
            supabase.table("properties")
            .select("id, property_name, property_code, sort_order, is_active")
            .eq("is_active", True)
            .order("sort_order")
            .order("property_name")
            .execute()
        )
    except Exception as e:
        logger.error(f"get_mate_properties failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="物件取得に失敗しました。")
    return res.data or []


@router.get("/{staff_id}/dashboard")
def get_mate_dashboard(staff_id: str, current_user: dict = Depends(require_admin_or_leader)):
    today = date.today()
    month_start, month_end = _month_range(today)

    try:
        staff_res = (
            supabase.table("staff_members")
            .select("*")
            .eq("id", staff_id)
            .limit(1)
            .execute()
        )
    except Exception as e:
        logger.error(f"mate dashboard staff lookup failed: staff_id={staff_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="スタッフ情報取得に失敗しました。")

    if not staff_res.data:
        raise HTTPException(status_code=404, detail="対象スタッフが見つかりません。")

    staff = staff_res.data[0]
    available_ids = [str(x) for x in (staff.get("available_property_ids") or [])]
    unchecked_ids = [str(x) for x in (staff.get("unchecked_property_ids") or [])]
    property_ids = list(dict.fromkeys(unchecked_ids + available_ids))

    property_names: list[str] = []
    if property_ids:
        try:
            prop_res = (
                supabase.table("properties")
                .select("id, property_name, sort_order")
                .in_("id", property_ids)
                .order("sort_order")
                .execute()
            )
            prop_map = {str(row.get("id")): str(row.get("property_name") or "") for row in prop_res.data or []}
            property_names = [prop_map[x] for x in property_ids if prop_map.get(x)]
        except Exception as e:
            logger.warning(f"mate dashboard property lookup failed: staff_id={staff_id} {e}")

    attendance_days = 0
    try:
        attendance_res = (
            supabase.table("attendance_logs")
            .select("work_date, staff_id, clock_in_at")
            .eq("staff_id", staff_id)
            .gte("work_date", month_start)
            .lte("work_date", month_end)
            .execute()
        )
        attendance_days = len({
            str(row.get("work_date"))
            for row in attendance_res.data or []
            if row.get("work_date") and row.get("clock_in_at")
        })
    except Exception as e:
        logger.warning(f"mate dashboard attendance lookup failed: staff_id={staff_id} {e}")

    try:
        task_res = (
            supabase.table("cleaning_tasks")
            .select("id, task_date, property_name, room_name, status, assigned_staff_ids, assigned_staff_id, cleaning_started_at, cleaning_completed_at")
            .gte("task_date", month_start)
            .lte("task_date", month_end)
            .execute()
        )
        current_stats = _task_stats(list(task_res.data or []), staff_id)
    except Exception as e:
        logger.warning(f"mate dashboard cleaning lookup failed: staff_id={staff_id} {e}")
        current_stats = {"cleaning_count": 0, "average_cleaning_minutes": None, "worked_property_names": []}

    monthly_history: list[dict[str, Any]] = []
    cursor = today.replace(day=1)
    for _ in range(3):
        start, end = _month_range(cursor)
        try:
            month_tasks_res = (
                supabase.table("cleaning_tasks")
                .select("id, task_date, property_name, room_name, status, assigned_staff_ids, assigned_staff_id, cleaning_started_at, cleaning_completed_at")
                .gte("task_date", start)
                .lte("task_date", end)
                .execute()
            )
            stats = _task_stats(list(month_tasks_res.data or []), staff_id)
        except Exception as e:
            logger.warning(f"mate dashboard monthly cleaning lookup failed: staff_id={staff_id} month={start[:7]} {e}")
            stats = {"cleaning_count": 0, "average_cleaning_minutes": None, "worked_property_names": []}

        month_attendance_days = 0
        try:
            month_att_res = (
                supabase.table("attendance_logs")
                .select("work_date, staff_id, clock_in_at")
                .eq("staff_id", staff_id)
                .gte("work_date", start)
                .lte("work_date", end)
                .execute()
            )
            month_attendance_days = len({
                str(row.get("work_date"))
                for row in month_att_res.data or []
                if row.get("work_date") and row.get("clock_in_at")
            })
        except Exception as e:
            logger.warning(f"mate dashboard monthly attendance lookup failed: staff_id={staff_id} month={start[:7]} {e}")

        monthly_history.append({
            "month": start[:7],
            "attendance_days": month_attendance_days,
            "cleaning_count": stats["cleaning_count"],
            "average_cleaning_minutes": stats["average_cleaning_minutes"],
        })
        cursor = (cursor - timedelta(days=1)).replace(day=1)

    return {
        "staff": {
            "id": staff.get("id"),
            "staff_code": staff.get("staff_code"),
            "staff_name": staff.get("staff_name"),
            "role": staff.get("role"),
            "is_active": staff.get("is_active"),
            "joined_date": _pick_staff_value(staff, ["joined_date", "join_date", "hire_date", "employment_start_date"]),
        },
        "month": month_start[:7],
        "attendance_days": attendance_days,
        "cleaning_count": current_stats["cleaning_count"],
        "average_cleaning_minutes": current_stats["average_cleaning_minutes"],
        "available_property_count": len(property_ids),
        "available_property_ids": property_ids,
        "available_property_names": property_names,
        "priority_property_ids": unchecked_ids,
        "quality_rank": _pick_staff_value(staff, ["quality_rank", "quality_level", "quality_grade"]),
        "response_level": _pick_staff_value(staff, ["response_level", "support_level", "対応力"]),
        "practical_level": _pick_staff_value(staff, ["practical_level", "work_level", "実務"]),
        "monthly_history": monthly_history,
    }


@router.get("/{staff_id}")
def get_mate_cartes(staff_id: str, current_user: dict = Depends(require_admin_or_leader)):
    try:
        res = (
            supabase.table("mate_cartes")
            .select("*")
            .eq("target_staff_id", staff_id)
            .order("record_date", desc=True)
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as e:
        logger.error(f"get_mate_cartes failed: staff_id={staff_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="メイトカルテ取得に失敗しました。")
    return res.data or []


@router.get("/{staff_id}/cleaning-summary")
def get_cleaning_summary(
    staff_id: str,
    record_date: str,
    current_user: dict = Depends(require_admin_or_leader),
):
    if not record_date:
        raise HTTPException(status_code=400, detail="record_date is required")
    try:
        res = (
            supabase.table("cleaning_tasks")
            .select("id, property_name, room_name, task_date, status, assigned_staff_ids, assigned_staff_id, cleaning_started_at, cleaning_completed_at")
            .eq("task_date", record_date)
            .execute()
        )
    except Exception as e:
        logger.error(f"get_cleaning_summary failed: staff_id={staff_id} date={record_date} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="清掃時間取得に失敗しました。")

    tasks = []
    started_values: list[str] = []
    completed_values: list[str] = []
    property_names: list[str] = []

    for row in res.data or []:
        ids = row.get("assigned_staff_ids") if isinstance(row.get("assigned_staff_ids"), list) else []
        single_id = row.get("assigned_staff_id")
        if staff_id not in ids and staff_id != single_id:
            continue
        tasks.append(row)
        property_name = row.get("property_name")
        if property_name and property_name not in property_names:
            property_names.append(property_name)
        started = row.get("cleaning_started_at")
        completed = row.get("cleaning_completed_at")
        if started:
            started_values.append(started)
        if completed:
            completed_values.append(completed)

    start_at = min(started_values) if started_values else None
    end_at = max(completed_values) if completed_values else None

    return {
        "staff_id": staff_id,
        "record_date": record_date,
        "task_count": len(tasks),
        "property_names": property_names,
        "cleaning_start_at": start_at,
        "cleaning_end_at": end_at,
        "tasks": tasks,
    }


@router.post("")
def create_mate_carte(
    target_staff_id: str = Body(...),
    record_date: str = Body(...),
    property_ids: list[str] = Body(default=[]),
    property_names: list[str] = Body(default=[]),
    cleaning_start_at: str | None = Body(None),
    cleaning_end_at: str | None = Body(None),
    guidance_category: str = Body(...),
    good_points: str = Body(""),
    correction_points: str = Body(""),
    handover_notes: str = Body(""),
    current_user: dict = Depends(require_admin_or_leader),
):
    if guidance_category not in ["清掃全般", "倉庫作業", "その他作業"]:
        raise HTTPException(status_code=400, detail="指導内容が不正です。")

    instructor = _get_current_staff(current_user)

    try:
        target_res = (
            supabase.table("staff_members")
            .select("id, staff_name, staff_code, role")
            .eq("id", target_staff_id)
            .limit(1)
            .execute()
        )
    except Exception as e:
        logger.error(f"mate target lookup failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="対象スタッフ取得に失敗しました。")

    if not target_res.data:
        raise HTTPException(status_code=404, detail="対象スタッフが見つかりません。")

    target = target_res.data[0]
    now = datetime.now(timezone.utc).isoformat()

    payload = {
        "target_staff_id": target_staff_id,
        "target_staff_name": target.get("staff_name") or "",
        "record_date": record_date,
        "instructor_id": instructor.get("id"),
        "instructor_name": instructor.get("staff_name") or "",
        "property_ids": property_ids or [],
        "property_names": property_names or [],
        "cleaning_start_at": cleaning_start_at,
        "cleaning_end_at": cleaning_end_at,
        "guidance_category": guidance_category,
        "good_points": good_points or "",
        "correction_points": correction_points or "",
        "handover_notes": handover_notes or "",
        "created_at": now,
        "updated_at": now,
    }

    try:
        res = supabase.table("mate_cartes").insert(payload).execute()
    except Exception as e:
        logger.error(f"create_mate_carte failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="メイトカルテ保存に失敗しました。")

    if not res.data:
        raise HTTPException(status_code=500, detail="メイトカルテ保存に失敗しました。")

    logger.info(f"create_mate_carte: target={target_staff_id} date={record_date}")
    return res.data[0]
