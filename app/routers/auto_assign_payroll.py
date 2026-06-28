from typing import Any

from app.db import supabase
from app.logger import get_logger
from app.routers import auto_assign_300pt

logger = get_logger(__name__)

DEFAULT_PAYROLL_TYPE = "piece"
_base_fetch_staffs = auto_assign_300pt._fetch_staffs
_base_sort_candidate_key = auto_assign_300pt._sort_candidate_key


def _fetch_payroll_types() -> dict[str, str]:
    try:
        res = supabase.table("staff_payroll_settings").select("staff_id, payroll_type, is_active").execute()
    except Exception as e:
        logger.warning(f"staff_payroll_settings lookup skipped: {e}")
        return {}

    result: dict[str, str] = {}
    for row in res.data or []:
        if row.get("is_active") is False:
            continue
        staff_id = str(row.get("staff_id") or "")
        payroll_type = str(row.get("payroll_type") or DEFAULT_PAYROLL_TYPE)
        if staff_id:
            result[staff_id] = payroll_type if payroll_type in ["piece", "hourly"] else DEFAULT_PAYROLL_TYPE
    return result


def fetch_staffs_with_payroll(target_date: str) -> list[dict[str, Any]]:
    staffs = _base_fetch_staffs(target_date)
    payroll_types = _fetch_payroll_types()
    for staff in staffs:
        staff_id = str(staff.get("id") or "")
        staff["payroll_type"] = payroll_types.get(staff_id, DEFAULT_PAYROLL_TYPE)
    return staffs


def payroll_order(staff: dict[str, Any]) -> int:
    return 0 if str(staff.get("payroll_type") or DEFAULT_PAYROLL_TYPE) == "piece" else 1


def sort_candidate_key_with_payroll(item, pid, count_by_staff_property, used_points, props_by_staff):
    _priority, staff = item
    return (payroll_order(staff),) + tuple(
        _base_sort_candidate_key(item, pid, count_by_staff_property, used_points, props_by_staff)
    )


auto_assign_300pt._fetch_staffs = fetch_staffs_with_payroll
auto_assign_300pt._sort_candidate_key = sort_candidate_key_with_payroll
logger.info("auto_assign payroll_type support applied")
