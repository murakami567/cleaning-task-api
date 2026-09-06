from collections import defaultdict

from app.db import supabase
from app.logger import get_logger

logger = get_logger(__name__)


def _to_int(value, default=0):
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def _to_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _time_to_minutes(value):
    text = str(value or "").strip()
    if not text or ":" not in text:
        return None
    try:
        hour, minute = text[:5].split(":")
        return int(hour) * 60 + int(minute)
    except Exception:
        return None


def _active_rate(rate, target_date):
    if not rate.get("is_active", True):
        return False
    valid_from = rate.get("valid_from")
    valid_to = rate.get("valid_to")
    if valid_from and target_date < str(valid_from):
        return False
    if valid_to and target_date > str(valid_to):
        return False
    return True


def _get_room_rate(room_rates, property_name, room_name, target_date):
    for rate in room_rates:
        if not _active_rate(rate, target_date):
            continue
        if rate.get("property_name") != property_name:
            continue
        if rate.get("room_name") != room_name:
            continue
        return _to_int(rate.get("rate"), 0)
    return None


def _get_property_rate(property_rates, property_name, target_date):
    for rate in property_rates:
        if not _active_rate(rate, target_date):
            continue
        if rate.get("property_name") != property_name:
            continue
        return _to_int(rate.get("rate"), 0)
    return 0


def _assigned_ids(task):
    ids = task.get("assigned_staff_ids") or []
    if not isinstance(ids, list):
        ids = []
    if not ids and task.get("assigned_staff_id"):
        ids = [task.get("assigned_staff_id")]
    return [str(value) for value in ids if value]


def _paid_time_from_worklogs(worklogs):
    """部屋ごとに重複保存される実働行を日次時間として1回だけ扱う。"""
    starts = []
    ends = []
    for row in worklogs:
        start = _time_to_minutes(row.get("work_start_time"))
        end = _time_to_minutes(row.get("end_time"))
        if start is None or end is None:
            continue
        if end <= start:
            continue
        starts.append(start)
        ends.append(end)

    if not starts or not ends:
        return {
            "minutes": 0,
            "hours": 0.0,
            "start_time": "",
            "end_time": "",
        }

    start = min(starts)
    end = max(ends)
    minutes = max(end - start, 0)
    return {
        "minutes": minutes,
        "hours": round(minutes / 60, 2),
        "start_time": f"{start // 60:02d}:{start % 60:02d}",
        "end_time": f"{end // 60:02d}:{end % 60:02d}",
    }


def recalculate_piece_daily_payroll(staff_id: str, target_date: str):
    """
    部屋単価スタッフが実働報告したタイミングで、その日を丸ごと再計算する。

    - 給与対象部屋: 本人に割り当てられ、status=完了 の cleaning_tasks
    - 清掃報酬: room_piece_rates 優先、なければ property_type_piece_rates
    - 複数人清掃: 部屋単価を担当人数で均等割り
    - 時給対象: 実働報告の最早 work_start_time ～ 最遅 end_time
      （部屋ごとの重複POSTは時間を重複加算しない）
    - 最低保証・交通費: staff_payroll_settings に従う
    - 既存の日次結果は staff/date 単位で削除して再作成する
    """
    staff_id = str(staff_id or "")
    target_date = str(target_date or "")[:10]
    if not staff_id or not target_date:
        return {"ok": False, "skipped": "missing_staff_or_date"}

    setting_res = (
        supabase.table("staff_payroll_settings")
        .select("*")
        .eq("staff_id", staff_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    if not setting_res.data:
        return {"ok": True, "skipped": "payroll_setting_not_found"}

    setting = setting_res.data[0]
    payroll_type = str(setting.get("payroll_type") or "piece")
    if payroll_type == "hourly":
        return {"ok": True, "skipped": "not_piece_staff"}

    staff_name = setting.get("staff_name") or ""
    hourly_rate = _to_int(setting.get("hourly_rate"), 1300)
    minimum_hours = _to_float(setting.get("minimum_hours"), 0)
    transportation_fee = _to_int(setting.get("transportation_fee"), 0)

    tasks_res = (
        supabase.table("cleaning_tasks")
        .select("*")
        .eq("task_date", target_date)
        .eq("status", "完了")
        .execute()
    )
    completed_tasks = [
        task for task in (tasks_res.data or [])
        if staff_id in _assigned_ids(task)
    ]

    room_rates = (
        supabase.table("room_piece_rates")
        .select("*")
        .eq("is_active", True)
        .execute()
    ).data or []
    property_rates = (
        supabase.table("property_type_piece_rates")
        .select("*")
        .eq("is_active", True)
        .execute()
    ).data or []

    property_rows = defaultdict(lambda: {
        "facility": "",
        "room_count": 0,
        "worker_count": 1,
        "cleaning_amount": 0,
        "room_details": [],
        "rates": [],
    })

    for task in completed_tasks:
        property_name = task.get("property_name") or ""
        room_name = task.get("room_name") or ""
        ids = _assigned_ids(task)
        worker_count = max(len(ids), 1)

        base_rate = _get_room_rate(
            room_rates, property_name, room_name, target_date
        )
        if base_rate is None:
            base_rate = _get_property_rate(
                property_rates, property_name, target_date
            )
        per_staff_amount = round(base_rate / worker_count)

        row = property_rows[property_name]
        row["facility"] = property_name
        row["room_count"] += 1
        row["worker_count"] = max(row["worker_count"], worker_count)
        row["cleaning_amount"] += per_staff_amount
        row["rates"].append(base_rate)
        row["room_details"].append(
            f"{room_name} ¥{per_staff_amount:,}"
            + (f"（{worker_count}名割）" if worker_count > 1 else "")
        )

    worklogs_res = (
        supabase.table("worklogs")
        .select("*")
        .eq("user_id", staff_id)
        .eq("work_date", target_date)
        .execute()
    )
    paid_time = _paid_time_from_worklogs(worklogs_res.data or [])
    work_hours = paid_time["hours"]
    hourly_amount = round(work_hours * hourly_rate)

    cleaning_total = sum(
        _to_int(row.get("cleaning_amount")) for row in property_rows.values()
    )
    base_amount = cleaning_total + hourly_amount
    minimum_guarantee = round(hourly_rate * minimum_hours)
    adjustment_amount = max(minimum_guarantee - base_amount, 0) if minimum_guarantee > 0 else 0

    # 実働再送信時は管理者が再確認できるよう未確定へ戻す。
    supabase.table("payroll_daily_results") \
        .delete() \
        .eq("staff_id", staff_id) \
        .eq("target_date", target_date) \
        .execute()

    rows = list(property_rows.values())
    if not rows:
        rows = [{
            "facility": "清掃外作業",
            "room_count": 0,
            "worker_count": 1,
            "cleaning_amount": 0,
            "room_details": [],
            "rates": [],
        }]

    payloads = []
    for index, row in enumerate(rows):
        is_first = index == 0
        rates = row.get("rates") or []
        unit_price = rates[0] if rates and all(rate == rates[0] for rate in rates) else 0

        notes = []
        if row.get("room_details"):
            notes.append("完了部屋: " + " / ".join(row["room_details"]))
        if is_first and paid_time["minutes"] > 0:
            notes.append(
                f"時給対象: {paid_time['start_time']}〜{paid_time['end_time']} "
                f"{work_hours:.2f}h × ¥{hourly_rate:,} = ¥{hourly_amount:,}"
            )

        row_cleaning = _to_int(row.get("cleaning_amount"))
        row_hourly = hourly_amount if is_first else 0
        row_adjustment = adjustment_amount if is_first else 0
        row_transport = transportation_fee if is_first else 0
        row_final = row_cleaning + row_hourly + row_adjustment + row_transport

        payloads.append({
            "target_date": target_date,
            "staff_id": staff_id,
            "staff_name": staff_name,
            "payroll_type": payroll_type,
            "facility": row.get("facility") or "",
            "room_count": _to_int(row.get("room_count")),
            "worker_count": _to_int(row.get("worker_count"), 1),
            "unit_price": _to_int(unit_price),
            "cleaning_amount": row_cleaning,
            "work_hours": work_hours if is_first else 0,
            "actual_hours": work_hours if is_first else 0,
            "hourly_rate": hourly_rate,
            "hourly_amount": row_hourly,
            "base_amount": base_amount if is_first else row_cleaning,
            "minimum_guarantee": minimum_guarantee if is_first else 0,
            "adjustment_amount": row_adjustment,
            "busy_season_allowance": "",
            "transportation_fee": row_transport,
            "final_amount": row_final,
            "status": "未確定",
            "note": " / ".join(notes),
        })

    inserted = (
        supabase.table("payroll_daily_results")
        .insert(payloads)
        .execute()
    ).data or []

    logger.info(
        "recalculate_piece_daily_payroll: staff_id=%s date=%s rooms=%s work_hours=%s total=%s",
        staff_id,
        target_date,
        len(completed_tasks),
        work_hours,
        sum(_to_int(row.get("final_amount")) for row in payloads),
    )

    return {
        "ok": True,
        "staff_id": staff_id,
        "target_date": target_date,
        "completed_room_count": len(completed_tasks),
        "work_hours": work_hours,
        "hourly_amount": hourly_amount,
        "cleaning_total": cleaning_total,
        "minimum_guarantee": minimum_guarantee,
        "adjustment_amount": adjustment_amount,
        "transportation_fee": transportation_fee,
        "data": inserted,
    }
