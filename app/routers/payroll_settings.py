from fastapi import APIRouter, Body, HTTPException

from app.db import supabase
from app.logger import get_logger

router = APIRouter(tags=["payroll"])
logger = get_logger(__name__)


@router.post("/payroll/settings/staff/upsert")
def upsert_staff_payroll_setting(
    staff_id: str = Body(...),
    staff_name: str | None = Body(None),
    payroll_type: str = Body("piece"),
    hourly_rate: int = Body(1300),
    minimum_hours: float | None = Body(None),
    minimum_guarantee: float | None = Body(None),
    transportation_fee: int = Body(0),
    note: str = Body(""),
    is_active: bool = Body(True),
):
    """Save payroll settings using staff_id as the source of truth.

    The admin screen only needs to send staff_id. staff_name is resolved from
    staff_members so a renamed staff member does not break payroll settings.
    minimum_guarantee is accepted for compatibility with the current admin UI
    and converted to minimum_hours because payroll calculation stores the
    guarantee as hours internally.
    """
    try:
        staff_res = (
            supabase.table("staff_members")
            .select("id,staff_name")
            .eq("id", staff_id)
            .limit(1)
            .execute()
        )
        staff = (staff_res.data or [None])[0]
        if not staff:
            raise HTTPException(status_code=404, detail="スタッフが見つかりません。")

        resolved_staff_name = str(staff.get("staff_name") or staff_name or "").strip()
        if not resolved_staff_name:
            raise HTTPException(status_code=400, detail="スタッフ名を取得できません。")

        normalized_payroll_type = "hourly" if payroll_type == "hourly" else "piece"
        normalized_hourly_rate = max(0, int(hourly_rate or 0))

        if minimum_hours is not None:
            normalized_minimum_hours = max(0.0, float(minimum_hours))
        elif minimum_guarantee is not None and normalized_hourly_rate > 0:
            normalized_minimum_hours = max(
                0.0, float(minimum_guarantee) / normalized_hourly_rate
            )
        else:
            normalized_minimum_hours = 0.0

        existing = (
            supabase.table("staff_payroll_settings")
            .select("*")
            .eq("staff_id", staff_id)
            .limit(1)
            .execute()
        )

        payload = {
            "staff_id": staff_id,
            "staff_name": resolved_staff_name,
            "payroll_type": normalized_payroll_type,
            "hourly_rate": normalized_hourly_rate,
            "minimum_hours": normalized_minimum_hours,
            "transportation_fee": max(0, int(transportation_fee or 0)),
            "note": note or "",
            "is_active": bool(is_active),
        }

        if existing.data:
            res = (
                supabase.table("staff_payroll_settings")
                .update(payload)
                .eq("id", existing.data[0]["id"])
                .execute()
            )
        else:
            res = supabase.table("staff_payroll_settings").insert(payload).execute()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"upsert_staff_payroll_setting failed: staff_id={staff_id} {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="スタッフ給与設定の保存に失敗しました。")

    if not res.data:
        raise HTTPException(status_code=500, detail="スタッフ給与設定の保存に失敗しました。")

    logger.info(
        "upsert_staff_payroll_setting: staff_id=%s staff_name=%s payroll_type=%s",
        staff_id,
        resolved_staff_name,
        normalized_payroll_type,
    )
    return res.data[0]
