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


STATUS_KEYS = {
    "status",
    "status_name",
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
    "work_pattern",
    "work_pattern_name",
    "calendar_type",
    "calendar_type_name",
    "request_type",
    "request_type_name",
    "application_type",
    "application_type_name",
}

BOOLEAN_STATUS_KEYS = {
    "is_holiday": "休み",
    "holiday_flg": "休み",
    "holiday_flag": "休み",
    "is_day_off": "休み",
    "day_off_flg": "休み",
    "is_paid_leave": "有給",
    "paid_leave_flg": "有給",
    "paid_leave_flag": "有給",
    "is_legal_holiday": "法定休日",
    "legal_holiday_flg": "法定休日",
    "is_prescribed_holiday": "所定休日",
    "prescribed_holiday_flg": "所定休日",
}


def _scalar_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    return ""


def _collect_status_values(value: Any, parent_key: str = "") -> list[str]:
    """Jinjerレスポンスのネスト階層を問わず、区分判定に使える値を抽出する。"""
    values: list[str] = []

    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = str(raw_key or "").strip().lower()

            if key in BOOLEAN_STATUS_KEYS and child in (True, 1, "1", "true", "True"):
                values.append(BOOLEAN_STATUS_KEYS[key])

            if key in STATUS_KEYS or any(
                token in key
                for token in (
                    "holiday",
                    "leave",
                    "vacation",
                    "day_off",
                    "dayoff",
                    "status",
                    "work_type",
                    "schedule_type",
                    "request_type",
                    "application_type",
                )
            ):
                scalar = _scalar_text(child)
                if scalar:
                    values.append(scalar)

            values.extend(_collect_status_values(child, key))

    elif isinstance(value, list):
        for child in value:
            values.extend(_collect_status_values(child, parent_key))

    elif parent_key in STATUS_KEYS:
        scalar = _scalar_text(value)
        if scalar:
            values.append(scalar)

    return values


def _extract_schedule_status(entry: dict[str, Any], schedule: dict[str, Any]) -> tuple[str, str]:
    labels = _collect_status_values(entry)
    if schedule is not entry:
        labels.extend(_collect_status_values(schedule))

    unique_labels = list(dict.fromkeys(label for label in labels if label))
    raw_label = " / ".join(unique_labels)
    compact = (
        raw_label.replace(" ", "")
        .replace("　", "")
        .replace("-", "")
        .replace("_", "")
        .lower()
    )

    # 優先順位が重要。「有給休暇」は一般の「休暇」より先、
    # 「法定休日・所定休日」は一般の「休日」より先に判定する。
    if any(word in compact for word in ("有給", "有休", "年休", "paidleave", "annualleave")):
        return "有給", raw_label

    if any(word in compact for word in ("法定休日", "法定休", "法定", "legalholiday")):
        return "定休", raw_label

    if any(word in compact for word in ("所定休日", "所定休", "所定", "prescribedholiday")):
        return "定休", raw_label

    if any(word in compact for word in ("休み", "休日", "公休", "dayoff")) or compact in {
        "休",
        "off",
    }:
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
    status_samples: dict[str, list[str]] = {}

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
                if status_source:
                    samples = status_samples.setdefault(status, [])
                    if status_source not in samples and len(samples) < 5:
                        samples.append(status_source[:300])

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
        f"samples={status_samples} params={selected_param_shape}"
    )
    return items
