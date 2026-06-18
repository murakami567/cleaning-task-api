import csv
import io
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import Response

from app.db import supabase
from app.logger import get_logger

router = APIRouter(prefix="/reports/monthly", tags=["monthly-reports"])
logger = get_logger(__name__)

PAGE_SIZE = 1000


def _month_range(month: str) -> tuple[str, str]:
    first = datetime.strptime(month, "%Y-%m").date()
    if first.month == 12:
        next_month = date(first.year + 1, 1, 1)
    else:
        next_month = date(first.year, first.month + 1, 1)
    return first.isoformat(), (next_month - timedelta(days=1)).isoformat()


def _fetch_all(table_name: str, select: str = "*", filters: list[tuple[str, str, Any]] | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start = 0
    while True:
        q = supabase.table(table_name).select(select)
        for op, col, value in filters or []:
            if op == "gte":
                q = q.gte(col, value)
            elif op == "lte":
                q = q.lte(col, value)
            elif op == "eq":
                q = q.eq(col, value)
        res = q.range(start, start + PAGE_SIZE - 1).execute()
        batch = res.data or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return rows


def _csv_response(filename: str, rows: list[dict[str, Any]], fieldnames: list[str]) -> Response:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in fieldnames})
    return Response(
        content=buf.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _staff_sort_key(row: dict[str, Any]) -> tuple[int, str]:
    try:
        order = int(row.get("display_order")) if row.get("display_order") is not None else 999999
    except Exception:
        order = 999999
    return order, str(row.get("staff_name") or "")


def _staff_maps() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    staffs = _fetch_all("staff_members")
    active_staffs = [s for s in staffs if s.get("is_active") is not False]
    active_staffs.sort(key=_staff_sort_key)
    by_id = {str(s.get("id")): s for s in staffs if s.get("id") is not None}
    by_code = {str(s.get("staff_code")): s for s in staffs if s.get("staff_code") is not None}
    return by_id, by_code, active_staffs


def _task_date(row: dict[str, Any]) -> str:
    return str(row.get("task_date") or row.get("cleaning_date") or row.get("date") or row.get("checkout_date") or "")[:10]


def _task_property(row: dict[str, Any]) -> str:
    return str(row.get("property_name") or row.get("property") or "未設定")


def _split_names(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if value is None:
        return []
    text = str(value).strip()
    if not text or text in ["-", "未割当", "未割り当て"]:
        return []
    parts = re.split(r"[、,，・/\s]+", text)
    return [p.strip() for p in parts if p.strip()]


def _assignee_names(row: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> list[str]:
    names = _split_names(row.get("assigned_staff_names"))
    if names:
        return names
    ids = row.get("assigned_staff_ids")
    if isinstance(ids, list):
        resolved = []
        for staff_id in ids:
            staff = by_id.get(str(staff_id))
            if staff and staff.get("staff_name"):
                resolved.append(str(staff.get("staff_name")))
        if resolved:
            return resolved
    names = _split_names(row.get("assigned_staff_name") or row.get("assignee_name") or row.get("assignee"))
    return names


def _monthly_cleaning_tasks(month: str) -> list[dict[str, Any]]:
    start, end = _month_range(month)
    rows = _fetch_all("cleaning_tasks", filters=[("gte", "task_date", start), ("lte", "task_date", end)])
    return [r for r in rows if _task_date(r)]


def _monthly_shift_entries(month: str) -> list[dict[str, Any]]:
    start, end = _month_range(month)
    try:
        return _fetch_all("shift_entries", filters=[("gte", "shift_date", start), ("lte", "shift_date", end)])
    except Exception:
        return []


def _monthly_attendance_logs(month: str) -> list[dict[str, Any]]:
    start, end = _month_range(month)
    return _fetch_all("attendance_logs", filters=[("gte", "work_date", start), ("lte", "work_date", end)])


def _work_days_in_month(month: str) -> int:
    start, end = _month_range(month)
    cur = datetime.strptime(start, "%Y-%m-%d").date()
    last = datetime.strptime(end, "%Y-%m-%d").date()
    count = 0
    while cur <= last:
        if cur.weekday() < 5:
            count += 1
        cur += timedelta(days=1)
    return count


@router.get("/attendance-rate.csv")
def attendance_rate_csv(month: str = Query(..., description="YYYY-MM")):
    by_id, by_code, active_staffs = _staff_maps()
    logs = _monthly_attendance_logs(month)
    shifts = _monthly_shift_entries(month)

    scheduled_dates: dict[str, set[str]] = defaultdict(set)
    attended_dates: dict[str, set[str]] = defaultdict(set)

    for entry in shifts:
        staff_id = str(entry.get("staff_id") or entry.get("staff_member_id") or "")
        staff_code = str(entry.get("staff_code") or "")
        staff = by_id.get(staff_id) or by_code.get(staff_code)
        if not staff:
            continue
        d = str(entry.get("shift_date") or entry.get("work_date") or entry.get("date") or "")[:10]
        if d:
            scheduled_dates[str(staff.get("id"))].add(d)

    for log in logs:
        staff_id = str(log.get("staff_id") or "")
        staff_code = str(log.get("staff_code") or "")
        staff = by_id.get(staff_id) or by_code.get(staff_code)
        if not staff:
            continue
        if log.get("clock_in_at") or log.get("attended_at"):
            d = str(log.get("work_date") or log.get("date") or "")[:10]
            if d:
                attended_dates[str(staff.get("id"))].add(d)

    default_days = _work_days_in_month(month)
    rows = []
    for staff in active_staffs:
        sid = str(staff.get("id"))
        scheduled = len(scheduled_dates.get(sid, set())) or default_days
        attended = len(attended_dates.get(sid, set()))
        rate = round(attended / scheduled * 100, 1) if scheduled else 0
        rows.append({
            "年月": month,
            "社員番号": staff.get("staff_code") or "",
            "アカウント名": staff.get("staff_name") or "",
            "予定日数": scheduled,
            "出勤日数": attended,
            "出勤率": f"{rate}%",
        })

    return _csv_response(f"{month}_account_attendance_rate.csv", rows, ["年月", "社員番号", "アカウント名", "予定日数", "出勤日数", "出勤率"])


@router.get("/account-property-assignments.csv")
def account_property_assignments_csv(month: str = Query(..., description="YYYY-MM")):
    by_id, _, active_staffs = _staff_maps()
    active_names = {str(s.get("staff_name")) for s in active_staffs}
    tasks = _monthly_cleaning_tasks(month)

    counts: dict[tuple[str, str], int] = defaultdict(int)
    properties = set()
    for task in tasks:
        prop = _task_property(task)
        properties.add(prop)
        for name in _assignee_names(task, by_id):
            if name in active_names:
                counts[(name, prop)] += 1

    prop_list = sorted(properties)
    rows = []
    for staff in active_staffs:
        name = str(staff.get("staff_name") or "")
        row = {"年月": month, "社員番号": staff.get("staff_code") or "", "アカウント名": name}
        total = 0
        for prop in prop_list:
            c = counts.get((name, prop), 0)
            row[prop] = c
            total += c
        row["合計"] = total
        rows.append(row)

    return _csv_response(f"{month}_account_property_assignments.csv", rows, ["年月", "社員番号", "アカウント名", *prop_list, "合計"])


@router.get("/property-cleaning-counts.csv")
def property_cleaning_counts_csv(month: str = Query(..., description="YYYY-MM")):
    tasks = _monthly_cleaning_tasks(month)
    counts: dict[str, int] = defaultdict(int)
    for task in tasks:
        counts[_task_property(task)] += 1

    rows = [{"年月": month, "物件": prop, "清掃数": count} for prop, count in sorted(counts.items())]
    rows.append({"年月": month, "物件": "合計", "清掃数": sum(counts.values())})
    return _csv_response(f"{month}_property_cleaning_counts.csv", rows, ["年月", "物件", "清掃数"])
