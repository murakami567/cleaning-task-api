from fastapi import APIRouter, Body, HTTPException
from datetime import date
import calendar
from collections import defaultdict

from app.db import supabase
from app.logger import get_logger

router = APIRouter(tags=["payroll"])
logger = get_logger(__name__)


# =========================================================
# 共通
# =========================================================

def month_range(year: int, month: int):
    start = date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    end = date(year, month, last_day)
    return start.isoformat(), end.isoformat()


def to_float(value, default=0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def to_int(value, default=0):
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def get_first(row: dict, keys: list[str], default=None):
    for key in keys:
        if key in row and row.get(key) is not None:
            return row.get(key)
    return default


def get_staff_setting(settings_by_staff: dict, staff_id: str):
    setting = settings_by_staff.get(staff_id)

    if setting:
        return setting

    return {
        "staff_id": staff_id,
        "staff_name": "",
        "payroll_type": "piece",
        "hourly_rate": 1300,
        "minimum_hours": 6,
        "transportation_fee": 500,
    }


def get_room_rate(room_rates: list[dict], property_name: str, room_name: str, target_date: str):
    for rate in room_rates:
        if not rate.get("is_active", True):
            continue

        if rate.get("property_name") != property_name:
            continue

        if rate.get("room_name") != room_name:
            continue

        valid_from = rate.get("valid_from")
        valid_to = rate.get("valid_to")

        if valid_from and target_date < valid_from:
            continue

        if valid_to and target_date > valid_to:
            continue

        return to_int(rate.get("rate"))

    return None


def get_property_type_rate(property_rates: list[dict], property_name: str, target_date: str):
    for rate in property_rates:
        if not rate.get("is_active", True):
            continue

        if rate.get("property_name") != property_name:
            continue

        valid_from = rate.get("valid_from")
        valid_to = rate.get("valid_to")

        if valid_from and target_date < valid_from:
            continue

        if valid_to and target_date > valid_to:
            continue

        return to_int(rate.get("rate"))

    return 0


def calc_task_piece_amount(task: dict, room_rates: list[dict], property_rates: list[dict]):
    property_name = task.get("property_name") or ""
    room_name = task.get("room_name") or ""
    task_date = task.get("task_date") or ""

    room_rate = get_room_rate(room_rates, property_name, room_name, task_date)

    if room_rate is not None:
        return room_rate

    return get_property_type_rate(property_rates, property_name, task_date)


def extract_worklog_date(row: dict):
    return get_first(row, ["work_date", "target_date", "date", "created_date"])


def extract_worklog_staff_id(row: dict):
    return get_first(row, ["staff_id", "user_id", "employee_id"])


def extract_worklog_staff_name(row: dict):
    return get_first(row, ["staff_name", "name", "employee_name"], "")


def extract_actual_hours(row: dict):
    return to_float(get_first(row, ["actual_hours", "actual_time", "実働時間", "work_actual_hours"], 0))


def extract_work_hours(row: dict):
    return to_float(get_first(row, ["work_hours", "task_hours", "作業時間", "non_cleaning_hours"], 0))


# =========================================================
# 設定取得
# =========================================================

@router.get("/payroll/settings")
def get_payroll_settings():
    try:
        staff_settings = (
            supabase.table("staff_payroll_settings")
            .select("*")
            .order("staff_name")
            .execute()
        )

        room_rates = (
            supabase.table("room_piece_rates")
            .select("*")
            .order("property_name")
            .order("room_name")
            .execute()
        )

        property_type_rates = (
            supabase.table("property_type_piece_rates")
            .select("*")
            .order("property_name")
            .order("property_type")
            .execute()
        )
    except Exception as e:
        logger.error(f"get_payroll_settings failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="給与設定の取得に失敗しました。")

    logger.info("get_payroll_settings: fetched")
    return {
        "staff_payroll_settings": staff_settings.data or [],
        "room_piece_rates": room_rates.data or [],
        "property_type_piece_rates": property_type_rates.data or [],
    }


# =========================================================
# 日別結果取得
# =========================================================

@router.get("/payroll/daily-results")
def get_payroll_daily_results(
    year: int,
    month: int,
    staff_id: str | None = None,
):
    start_date, end_date = month_range(year, month)

    try:
        query = (
            supabase.table("payroll_daily_results")
            .select("*")
            .gte("target_date", start_date)
            .lte("target_date", end_date)
            .order("target_date")
            .order("staff_name")
        )

        if staff_id:
            query = query.eq("staff_id", staff_id)

        res = query.execute()
    except Exception as e:
        logger.error(f"get_payroll_daily_results failed: year={year} month={month} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="給与計算結果の取得に失敗しました。")

    logger.info(f"get_payroll_daily_results: year={year} month={month} count={len(res.data or [])}")
    return res.data or []


# =========================================================
# 月別給与計算
# =========================================================

@router.post("/payroll/calculate-monthly")
def calculate_monthly_payroll(
    year: int = Body(...),
    month: int = Body(...),
):
    start_date, end_date = month_range(year, month)

    # -----------------------------
    # 設定取得
    # -----------------------------
    staff_settings_res = (
        supabase.table("staff_payroll_settings")
        .select("*")
        .eq("is_active", True)
        .execute()
    )

    room_rates_res = (
        supabase.table("room_piece_rates")
        .select("*")
        .eq("is_active", True)
        .execute()
    )

    property_rates_res = (
        supabase.table("property_type_piece_rates")
        .select("*")
        .eq("is_active", True)
        .execute()
    )

    staff_settings = staff_settings_res.data or []
    room_rates = room_rates_res.data or []
    property_rates = property_rates_res.data or []

    settings_by_staff = {
        str(row.get("staff_id")): row
        for row in staff_settings
        if row.get("staff_id")
    }

    # -----------------------------
    # 完了清掃タスク取得
    # -----------------------------
    tasks_res = (
        supabase.table("cleaning_tasks")
        .select("*")
        .eq("status", "完了")
        .gte("task_date", start_date)
        .lte("task_date", end_date)
        .execute()
    )

    cleaning_tasks = tasks_res.data or []

    # -----------------------------
    # 実働報告取得
    # -----------------------------
    # worklogs の列名は既存入力に合わせて柔軟に読む
    worklogs_res = (
        supabase.table("worklogs")
        .select("*")
        .gte("work_date", start_date)
        .lte("work_date", end_date)
        .execute()
    )

    worklogs = worklogs_res.data or []

    # staff_id + date 単位
    worklog_by_staff_date = defaultdict(lambda: {
        "actual_hours": 0,
        "work_hours": 0,
        "staff_name": "",
    })

    for row in worklogs:
        work_date = extract_worklog_date(row)
        staff_id = extract_worklog_staff_id(row)

        if not work_date or not staff_id:
            continue

        key = (str(staff_id), str(work_date))

        worklog_by_staff_date[key]["actual_hours"] += extract_actual_hours(row)
        worklog_by_staff_date[key]["work_hours"] += extract_work_hours(row)

        staff_name = extract_worklog_staff_name(row)
        if staff_name:
            worklog_by_staff_date[key]["staff_name"] = staff_name

    # -----------------------------
    # 清掃報酬を staff/date/property 単位で集計
    # -----------------------------
    cleaning_group = defaultdict(lambda: {
        "staff_id": "",
        "staff_name": "",
        "target_date": "",
        "facility": "",
        "room_count": 0,
        "worker_count": 1,
        "unit_price": 0,
        "cleaning_amount": 0,
    })

    for task in cleaning_tasks:
        task_date = task.get("task_date")
        property_name = task.get("property_name") or ""
        room_name = task.get("room_name") or ""

        assigned_ids = task.get("assigned_staff_ids") or []
        assigned_names = task.get("assigned_staff_names") or []

        if not isinstance(assigned_ids, list):
            assigned_ids = []

        if not isinstance(assigned_names, list):
            assigned_names = []

        if len(assigned_ids) == 0 and task.get("assigned_staff_id"):
            assigned_ids = [task.get("assigned_staff_id")]
            assigned_names = [task.get("assigned_staff_name") or ""]

        if len(assigned_ids) == 0:
            continue

        worker_count = max(len(assigned_ids), 1)
        base_rate = calc_task_piece_amount(task, room_rates, property_rates)
        per_staff_amount = round(base_rate / worker_count)

        for index, staff_id in enumerate(assigned_ids):
            staff_id = str(staff_id)
            staff_name = ""

            if index < len(assigned_names):
                staff_name = assigned_names[index] or ""

            setting = get_staff_setting(settings_by_staff, staff_id)
            payroll_type = setting.get("payroll_type", "piece")

            # 時給計算スタッフは清掃単価を使わない
            if payroll_type == "hourly":
                continue

            key = (staff_id, task_date, property_name)

            cleaning_group[key]["staff_id"] = staff_id
            cleaning_group[key]["staff_name"] = staff_name or setting.get("staff_name") or ""
            cleaning_group[key]["target_date"] = task_date
            cleaning_group[key]["facility"] = property_name
            cleaning_group[key]["room_count"] += 1
            cleaning_group[key]["worker_count"] = worker_count
            cleaning_group[key]["unit_price"] = base_rate
            cleaning_group[key]["cleaning_amount"] += per_staff_amount

    # -----------------------------
    # staff/date 全体を作る
    # -----------------------------
    staff_date_keys = set()

    for key in cleaning_group.keys():
        staff_id, target_date, _facility = key
        staff_date_keys.add((staff_id, target_date))

    for key in worklog_by_staff_date.keys():
        staff_id, target_date = key
        staff_date_keys.add((staff_id, target_date))

    results = []

    # 既存計算結果を削除して再作成
    try:
        supabase.table("payroll_daily_results") \
            .delete() \
            .gte("target_date", start_date) \
            .lte("target_date", end_date) \
            .execute()
    except Exception as e:
        logger.error(f"calculate_monthly_payroll delete failed: year={year} month={month} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"delete old payroll results failed: {str(e)}")

    for staff_id, target_date in sorted(staff_date_keys, key=lambda x: (x[1], x[0])):
        setting = get_staff_setting(settings_by_staff, staff_id)
        payroll_type = setting.get("payroll_type", "piece")
        hourly_rate = to_int(setting.get("hourly_rate"), 1300)
        minimum_hours = to_float(setting.get("minimum_hours"), 0)
        transportation_fee = to_int(setting.get("transportation_fee"), 0)

        worklog = worklog_by_staff_date.get((staff_id, target_date), {})
        actual_hours = to_float(worklog.get("actual_hours"), 0)
        work_hours = to_float(worklog.get("work_hours"), 0)

        staff_name = (
            worklog.get("staff_name")
            or setting.get("staff_name")
            or ""
        )

        facility_rows = [
            value
            for key, value in cleaning_group.items()
            if key[0] == staff_id and key[1] == target_date
        ]

        # -----------------------------
        # 時給スタッフ
        # -----------------------------
        if payroll_type == "hourly":
            hourly_amount = round(actual_hours * hourly_rate)
            final_amount = hourly_amount + transportation_fee

            payload = {
                "target_date": target_date,
                "staff_id": staff_id,
                "staff_name": staff_name,
                "payroll_type": payroll_type,

                "facility": "時給",
                "room_count": 0,
                "worker_count": 1,
                "unit_price": 0,
                "cleaning_amount": 0,

                "work_hours": 0,
                "actual_hours": actual_hours,
                "hourly_rate": hourly_rate,
                "hourly_amount": hourly_amount,

                "base_amount": hourly_amount,
                "minimum_guarantee": 0,
                "adjustment_amount": 0,

                "busy_season_allowance": "",
                "transportation_fee": transportation_fee,
                "final_amount": final_amount,
                "status": "未確定",
                "note": "",
            }

            results.append(payload)
            continue

        # -----------------------------
        # 単価スタッフ
        # -----------------------------
        cleaning_total = sum(to_int(row.get("cleaning_amount")) for row in facility_rows)
        hourly_amount = round(work_hours * hourly_rate)

        base_amount = cleaning_total + hourly_amount
        minimum_guarantee = round(hourly_rate * minimum_hours)

        adjustment_amount = 0
        if minimum_guarantee > 0 and base_amount < minimum_guarantee:
            adjustment_amount = minimum_guarantee - base_amount

        final_total = base_amount + adjustment_amount + transportation_fee

        # 清掃がないが作業時間だけある場合
        if not facility_rows:
            facility_rows = [{
                "staff_id": staff_id,
                "staff_name": staff_name,
                "target_date": target_date,
                "facility": "清掃外作業",
                "room_count": 0,
                "worker_count": 1,
                "unit_price": 0,
                "cleaning_amount": 0,
            }]

        # 交通費・時給・最低保証はその日の先頭行にまとめる
        for index, row in enumerate(facility_rows):
            is_first = index == 0

            row_cleaning_amount = to_int(row.get("cleaning_amount"))

            row_hourly_amount = hourly_amount if is_first else 0
            row_adjustment = adjustment_amount if is_first else 0
            row_transport = transportation_fee if is_first else 0

            row_final = (
                row_cleaning_amount
                + row_hourly_amount
                + row_adjustment
                + row_transport
            )

            payload = {
                "target_date": target_date,
                "staff_id": staff_id,
                "staff_name": row.get("staff_name") or staff_name,
                "payroll_type": payroll_type,

                "facility": row.get("facility"),
                "room_count": to_int(row.get("room_count")),
                "worker_count": to_int(row.get("worker_count"), 1),
                "unit_price": to_int(row.get("unit_price")),
                "cleaning_amount": row_cleaning_amount,

                "work_hours": work_hours if is_first else 0,
                "actual_hours": actual_hours if is_first else 0,
                "hourly_rate": hourly_rate,
                "hourly_amount": row_hourly_amount,

                "base_amount": base_amount if is_first else row_cleaning_amount,
                "minimum_guarantee": minimum_guarantee if is_first else 0,
                "adjustment_amount": row_adjustment,

                "busy_season_allowance": "",
                "transportation_fee": row_transport,
                "final_amount": row_final,
                "status": "未確定",
                "note": "",
            }

            results.append(payload)

    if results:
        try:
            insert_res = supabase.table("payroll_daily_results").insert(results).execute()
            inserted = insert_res.data or []
        except Exception as e:
            logger.error(f"calculate_monthly_payroll insert failed: year={year} month={month} {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"insert payroll results failed: {str(e)}")
    else:
        inserted = []

    logger.info(f"calculate_monthly_payroll: year={year} month={month} count={len(inserted)}")
    return {
        "ok": True,
        "year": year,
        "month": month,
        "start_date": start_date,
        "end_date": end_date,
        "count": len(inserted),
        "data": inserted,
    }


# =========================================================
# 確定・解除
# =========================================================

@router.post("/payroll/daily-results/update-status")
def update_payroll_daily_status(
    result_ids: list[str] = Body(...),
    status: str = Body(...),
):
    if not result_ids:
        raise HTTPException(status_code=400, detail="result_ids is required")

    if status not in ["未確定", "確定済"]:
        raise HTTPException(status_code=400, detail="invalid status")

    try:
        res = (
            supabase.table("payroll_daily_results")
            .update({"status": status})
            .in_("id", result_ids)
            .execute()
        )
    except Exception as e:
        logger.error(f"update_payroll_daily_status failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="ステータスの更新に失敗しました。")

    logger.info(f"update_payroll_daily_status: count={len(result_ids)} status={status}")
    return {
        "ok": True,
        "status": status,
        "data": res.data or [],
    }
@router.post("/payroll/settings/staff/upsert")
def upsert_staff_payroll_setting(
    staff_id: str = Body(...),
    staff_name: str = Body(...),
    payroll_type: str = Body("piece"),
    hourly_rate: int = Body(1300),
    minimum_hours: float = Body(6),
    transportation_fee: int = Body(0),
    note: str = Body(""),
):
    existing = (
        supabase.table("staff_payroll_settings")
        .select("*")
        .eq("staff_id", staff_id)
        .eq("is_active", True)
        .execute()
    )

    payload = {
        "staff_id": staff_id,
        "staff_name": staff_name,
        "payroll_type": payroll_type,
        "hourly_rate": hourly_rate,
        "minimum_hours": minimum_hours,
        "transportation_fee": transportation_fee,
        "note": note,
        "is_active": True,
    }

    try:
        if existing.data:
            res = (
                supabase.table("staff_payroll_settings")
                .update(payload)
                .eq("id", existing.data[0]["id"])
                .execute()
            )
        else:
            res = supabase.table("staff_payroll_settings").insert(payload).execute()
    except Exception as e:
        logger.error(f"upsert_staff_payroll_setting failed: staff_id={staff_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="staff payroll setting save failed")

    if not res.data:
        raise HTTPException(status_code=500, detail="staff payroll setting save failed")

    logger.info(f"upsert_staff_payroll_setting: staff_id={staff_id}")
    return res.data[0]

@router.post("/payroll/rates/room/upsert")
def upsert_room_piece_rate(
    property_id: str | None = Body(None),
    property_name: str = Body(...),
    room_id: str | None = Body(None),
    room_name: str = Body(...),
    room_key: str | None = Body(None),
    rate: int = Body(...),
    note: str = Body(""),
):
    existing = (
        supabase.table("room_piece_rates")
        .select("*")
        .eq("property_name", property_name)
        .eq("room_name", room_name)
        .eq("is_active", True)
        .execute()
    )

    payload = {
        "property_id": property_id,
        "property_name": property_name,
        "room_id": room_id,
        "room_name": room_name,
        "room_key": room_key,
        "rate": rate,
        "note": note,
        "is_active": True,
    }

    try:
        if existing.data:
            res = (
                supabase.table("room_piece_rates")
                .update(payload)
                .eq("id", existing.data[0]["id"])
                .execute()
            )
        else:
            res = supabase.table("room_piece_rates").insert(payload).execute()
    except Exception as e:
        logger.error(f"upsert_room_piece_rate failed: {property_name}/{room_name} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="room rate save failed")

    if not res.data:
        raise HTTPException(status_code=500, detail="room rate save failed")

    logger.info(f"upsert_room_piece_rate: {property_name}/{room_name} rate={rate}")
    return res.data[0]

@router.post("/payroll/rates/property-type/upsert")
def upsert_property_type_piece_rate(
    property_id: str | None = Body(None),
    property_name: str = Body(...),
    property_type: str = Body("通常"),
    rate: int = Body(...),
    note: str = Body(""),
):
    existing = (
        supabase.table("property_type_piece_rates")
        .select("*")
        .eq("property_name", property_name)
        .eq("property_type", property_type)
        .eq("is_active", True)
        .execute()
    )

    payload = {
        "property_id": property_id,
        "property_name": property_name,
        "property_type": property_type,
        "rate": rate,
        "note": note,
        "is_active": True,
    }

    try:
        if existing.data:
            res = (
                supabase.table("property_type_piece_rates")
                .update(payload)
                .eq("id", existing.data[0]["id"])
                .execute()
            )
        else:
            res = supabase.table("property_type_piece_rates").insert(payload).execute()
    except Exception as e:
        logger.error(f"upsert_property_type_piece_rate failed: {property_name} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="property type rate save failed")

    if not res.data:
        raise HTTPException(status_code=500, detail="property type rate save failed")

    logger.info(f"upsert_property_type_piece_rate: {property_name} rate={rate}")
    return res.data[0]
