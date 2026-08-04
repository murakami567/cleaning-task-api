from typing import Any

from fastapi import HTTPException

from app.logger import get_logger
from app.services.jinjer_service import (
    JINJER_WORK_SCHEDULE_ENDPOINT,
    _authorized_get,
    _extract_page_data,
    _work_schedule_param_candidates,
)

logger = get_logger(__name__)


def _first_value(rows: list[dict[str, Any]], keys: list[str]) -> Any:
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in keys:
            value = row.get(key)
            if value not in (None, ""):
                return value
    return None


def _text_values(value: Any) -> list[str]:
    values: list[str] = []
    if isinstance(value, str):
        text = value.strip()
        if text:
            values.append(text)
    elif isinstance(value, (int, float, bool)):
        values.append(str(value))
    elif isinstance(value, dict):
        for key in (
            "name",
            "label",
            "title",
            "display_name",
            "type_name",
            "status_name",
            "holiday_name",
            "leave_name",
            "value",
            "code",
            "type",
            "status",
        ):
            if key in value:
                values.extend(_text_values(value.get(key)))
    elif isinstance(value, list):
        for item in value:
            values.extend(_text_values(item))
    return values


def _extract_schedule_status(entry: dict[str, Any], schedule: dict[str, Any]) -> tuple[str, str]:
    sources = [entry, schedule]
    candidate_keys = [
        "status_name",
        "status",
        "schedule_status",
        "schedule_status_name",
        "schedule_type",
        "schedule_type_name",
        "work_type",
        "work_type_name",
        "work_day_type",
        "work_day_type_name",
        "day_type",
        "day_type_name",
        "holiday",
        "holiday_name",
        "holiday_type",
        "holiday_type_name",
        "leave",
        "leave_name",
        "leave_type",
        "leave_type_name",
        "vacation",
        "vacation_name",
        "vacation_type",
        "vacation_type_name",
        "absence",
        "absence_name",
        "attendance_type",
        "attendance_type_name",
        "shift_type",
        "shift_type_name",
    ]

    labels: list[str] = []
    for source in sources:
        for key in candidate_keys:
            if key in source:
                labels.extend(_text_values(source.get(key)))

    raw_label = " / ".join(dict.fromkeys(label for label in labels if label))
    compact = raw_label.replace(" ", "").replace("　", "").lower()

    if "有給" in compact or "年休" in compact or "paidleave" in compact or "paid_leave" in compact:
        return "有給", raw_label
    if "法定" in compact or "所定" in compact:
        return "定休", raw_label
    if (
        "休み" in compact
        or "休日" in compact
        or "公休" in compact
        or compact in {"休", "off", "dayoff", "day_off"}
    ):
        return "休み", raw_label

    return "出勤", raw_label


def fetch_work_schedules_with_status(month: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page = 1
    safety_max_pages = 200
    selected_param_shape: dict[str, Any] | None = None
    last_status: int | None = None
    last_text = ""
    tried_shapes: list[dict[str, Any]] = []

    while page <= safety_max_pages:
        param_candidates = (
            [selected_param_shape | {"page": page}]
            if selected_param_shape
            else _work_schedule_param_candidates(month, page)
        )
        response = None
        used_params: dict[str, Any] | None = None

        for params in param_candidates:
            tried_shapes.append({key: value for key, value in params.items() if key != "page"})
            try:
                candidate = _authorized_get(
                    JINJER_WORK_SCHEDULE_ENDPOINT,
                    params=params,
                    timeout=60,
                )
            except Exception as exc:
                logger.error(
                    f"jinjer status-aware schedule request failed: page={page} params={params} {exc}",
                    exc_info=True,
                )
                raise HTTPException(status_code=502, detail="Jinjer シフト取得に失敗しました。")

            if candidate.status_code == 200:
                response = candidate
                used_params = params
                if selected_param_shape is None:
                    selected_param_shape = {
                        key: value for key, value in params.items() if key != "page"
                    }
                break

            last_status = candidate.status_code
            last_text = candidate.text[:500]
            if candidate.status_code not in (400, 404, 422):
                break

        if response is None or used_params is None:
            logger.error(
                "jinjer status-aware schedules failed "
                f"status={last_status} endpoint={JINJER_WORK_SCHEDULE_ENDPOINT} "
                f"tried={tried_shapes[:20]} response={last_text}"
            )
            raise HTTPException(status_code=502, detail=f"Jinjer シフト取得失敗: {last_status}")

        body = response.json() or {}
        page_data = _extract_page_data(body)

        for employee in page_data:
            if not isinstance(employee, dict):
                continue

            employee_id = str(
                employee.get("employee_id")
                or employee.get("employee-id")
                or employee.get("staff_id")
                or employee.get("employee_code")
                or employee.get("code")
                or ""
            ).strip()
            if not employee_id:
                continue

            schedules = (
                employee.get("work_schedules")
                or employee.get("schedules")
                or employee.get("workSchedules")
                or []
            )
            for entry in schedules:
                if not isinstance(entry, dict):
                    continue

                schedule_date = (
                    entry.get("date")
                    or entry.get("work_date")
                    or entry.get("target_date")
                )
                schedule = entry.get("work_schedule") or entry.get("schedule") or entry
                if not isinstance(schedule, dict):
                    schedule = entry

                start = (
                    schedule.get("start")
                    or schedule.get("start_time")
                    or schedule.get("begin_time")
                )
                end = (
                    schedule.get("end")
                    or schedule.get("end_time")
                    or schedule.get("finish_time")
                )
                if not schedule_date:
                    continue

                status, status_source = _extract_schedule_status(entry, schedule)
                items.append(
                    {
                        "employee_id": employee_id,
                        "date": str(schedule_date)[:10],
                        "start": start,
                        "end": end,
                        "status": status,
                        "status_source": status_source,
                    }
                )

        if len(page_data) < 20 or "page" not in used_params:
            break
        page += 1

    status_counts: dict[str, int] = {}
    for item in items:
        status = str(item.get("status") or "出勤")
        status_counts[status] = status_counts.get(status, 0) + 1

    logger.info(
        "jinjer status-aware schedules fetched: "
        f"month={month} pages={page} items={len(items)} statuses={status_counts} "
        f"params={selected_param_shape}"
    )
    return items
