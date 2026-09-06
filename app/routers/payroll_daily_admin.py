from fastapi import APIRouter, Body, HTTPException

from app.db import supabase
from app.logger import get_logger

router = APIRouter(tags=["payroll-daily-admin"])
logger = get_logger(__name__)


def _to_int(value, default=0):
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


@router.post("/payroll/daily-results/update")
def update_payroll_daily_result(
    result_id: str = Body(...),
    cleaning_amount: int | None = Body(None),
    actual_hours: float | None = Body(None),
    hourly_rate: int | None = Body(None),
    hourly_amount: int | None = Body(None),
    adjustment_amount: int | None = Body(None),
    transportation_fee: int | None = Body(None),
    note: str | None = Body(None),
    status: str | None = Body(None),
):
    existing = (
        supabase.table("payroll_daily_results")
        .select("*")
        .eq("id", result_id)
        .limit(1)
        .execute()
    )
    if not existing.data:
        raise HTTPException(status_code=404, detail="給与日次データが見つかりません。")

    row = existing.data[0]
    payload = {}
    values = {
        "cleaning_amount": cleaning_amount,
        "actual_hours": actual_hours,
        "hourly_rate": hourly_rate,
        "hourly_amount": hourly_amount,
        "adjustment_amount": adjustment_amount,
        "transportation_fee": transportation_fee,
        "note": note,
        "status": status,
    }
    for key, value in values.items():
        if value is not None:
            payload[key] = value

    next_cleaning = _to_int(payload.get("cleaning_amount", row.get("cleaning_amount")))
    next_hourly = _to_int(payload.get("hourly_amount", row.get("hourly_amount")))
    next_adjustment = _to_int(payload.get("adjustment_amount", row.get("adjustment_amount")))
    next_transport = _to_int(payload.get("transportation_fee", row.get("transportation_fee")))

    payload["base_amount"] = next_cleaning + next_hourly
    payload["final_amount"] = next_cleaning + next_hourly + next_adjustment + next_transport
    if "actual_hours" in payload:
        payload["work_hours"] = payload["actual_hours"]

    try:
        res = (
            supabase.table("payroll_daily_results")
            .update(payload)
            .eq("id", result_id)
            .execute()
        )
    except Exception as e:
        logger.error(f"update_payroll_daily_result failed: id={result_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="給与日次データの更新に失敗しました。")

    return {"ok": True, "data": (res.data or [None])[0]}


@router.post("/payroll/daily-results/delete")
def delete_payroll_daily_result(result_id: str = Body(..., embed=True)):
    try:
        res = (
            supabase.table("payroll_daily_results")
            .delete()
            .eq("id", result_id)
            .execute()
        )
    except Exception as e:
        logger.error(f"delete_payroll_daily_result failed: id={result_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="給与日次データの削除に失敗しました。")

    return {"ok": True, "data": res.data or []}
