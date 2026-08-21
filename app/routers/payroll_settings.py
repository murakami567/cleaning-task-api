from fastapi import APIRouter, Body, HTTPException

from app.db import supabase
from app.logger import get_logger

router = APIRouter(tags=["payroll"])
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


def _first(data):
    rows = data or []
    return rows[0] if rows else None


def _resolve_property(property_id: str):
    if not property_id:
        return None
    res = (
        supabase.table("properties")
        .select("id,property_name")
        .eq("id", property_id)
        .limit(1)
        .execute()
    )
    return _first(res.data)


def _resolve_room(room_id: str):
    if not room_id:
        return None
    res = (
        supabase.table("rooms")
        .select("id,property_id,room_name,room_key")
        .eq("id", room_id)
        .limit(1)
        .execute()
    )
    return _first(res.data)


@router.get("/payroll/settings")
def get_payroll_settings_compat():
    """Return payroll settings in the field names used by the admin UI.

    DB remains the source of truth. This endpoint only adapts names/derived values
    so existing records are displayed without rewriting stored data.
    """
    try:
        staff_res = (
            supabase.table("staff_payroll_settings")
            .select("*")
            .order("staff_name")
            .execute()
        )
        room_res = (
            supabase.table("room_piece_rates")
            .select("*")
            .order("property_name")
            .order("room_name")
            .execute()
        )
        property_res = (
            supabase.table("property_type_piece_rates")
            .select("*")
            .order("property_name")
            .order("property_type")
            .execute()
        )
    except Exception as e:
        logger.error(f"get_payroll_settings_compat failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="給与設定の取得に失敗しました。")

    staff_rows = []
    for row in staff_res.data or []:
        hourly_rate = _to_int(row.get("hourly_rate"), 0)
        minimum_hours = _to_float(row.get("minimum_hours"), 0)
        staff_rows.append(
            {
                **row,
                "payroll_type": (
                    "hourly" if row.get("payroll_type") == "hourly" else "piece_rate"
                ),
                "minimum_guarantee": round(hourly_rate * minimum_hours),
            }
        )

    room_rows = []
    for row in room_res.data or []:
        room_rows.append(
            {
                **row,
                "unit_price": _to_int(row.get("rate"), 0),
                "busy_season_allowance": row.get("busy_season_allowance") or "",
            }
        )

    property_rows = []
    for row in property_res.data or []:
        property_rows.append(
            {
                **row,
                "work_type": row.get("property_type") or "",
                "unit_price": _to_int(row.get("rate"), 0),
            }
        )

    logger.info(
        "get_payroll_settings_compat: staff=%s room=%s property_type=%s",
        len(staff_rows),
        len(room_rows),
        len(property_rows),
    )
    return {
        "staff_payroll_settings": staff_rows,
        "room_piece_rates": room_rows,
        "property_type_piece_rates": property_rows,
    }


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
    try:
        staff_res = (
            supabase.table("staff_members")
            .select("id,staff_name")
            .eq("id", staff_id)
            .limit(1)
            .execute()
        )
        staff = _first(staff_res.data)
        if not staff:
            raise HTTPException(status_code=404, detail="スタッフが見つかりません。")

        resolved_staff_name = str(staff.get("staff_name") or staff_name or "").strip()
        if not resolved_staff_name:
            raise HTTPException(status_code=400, detail="スタッフ名を取得できません。")

        normalized_payroll_type = "hourly" if payroll_type == "hourly" else "piece"
        normalized_hourly_rate = max(0, _to_int(hourly_rate, 0))

        if minimum_hours is not None:
            normalized_minimum_hours = max(0.0, _to_float(minimum_hours, 0))
        elif minimum_guarantee is not None and normalized_hourly_rate > 0:
            normalized_minimum_hours = max(
                0.0, _to_float(minimum_guarantee, 0) / normalized_hourly_rate
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
            "transportation_fee": max(0, _to_int(transportation_fee, 0)),
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

    return res.data[0]


@router.post("/payroll/rates/room/upsert")
def upsert_room_piece_rate_compat(
    property_id: str = Body(...),
    room_id: str = Body(...),
    unit_price: int = Body(0),
    busy_season_allowance: str = Body(""),
    is_active: bool = Body(True),
):
    try:
        property_row = _resolve_property(property_id)
        room_row = _resolve_room(room_id)
        if not property_row:
            raise HTTPException(status_code=404, detail="物件が見つかりません。")
        if not room_row:
            raise HTTPException(status_code=404, detail="部屋が見つかりません。")
        if str(room_row.get("property_id")) != str(property_id):
            raise HTTPException(status_code=400, detail="物件と部屋の組み合わせが一致しません。")

        existing = (
            supabase.table("room_piece_rates")
            .select("*")
            .eq("room_id", room_id)
            .limit(1)
            .execute()
        )

        payload = {
            "property_id": property_id,
            "property_name": property_row.get("property_name") or "",
            "room_id": room_id,
            "room_name": room_row.get("room_name") or "",
            "room_key": room_row.get("room_key") or room_row.get("room_name") or "",
            "rate": max(0, _to_int(unit_price, 0)),
            "note": busy_season_allowance or "",
            "is_active": bool(is_active),
        }

        if existing.data:
            res = (
                supabase.table("room_piece_rates")
                .update(payload)
                .eq("id", existing.data[0]["id"])
                .execute()
            )
        else:
            res = supabase.table("room_piece_rates").insert(payload).execute()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"upsert_room_piece_rate_compat failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="部屋単価の保存に失敗しました。")

    if not res.data:
        raise HTTPException(status_code=500, detail="部屋単価の保存に失敗しました。")
    return res.data[0]


@router.post("/payroll/rates/property-type/upsert")
def upsert_property_type_piece_rate_compat(
    property_id: str = Body(...),
    work_type: str = Body(...),
    unit_price: int = Body(0),
    is_active: bool = Body(True),
):
    try:
        property_row = _resolve_property(property_id)
        if not property_row:
            raise HTTPException(status_code=404, detail="物件が見つかりません。")

        normalized_work_type = str(work_type or "").strip()
        if not normalized_work_type:
            raise HTTPException(status_code=400, detail="作業種別を入力してください。")

        existing = (
            supabase.table("property_type_piece_rates")
            .select("*")
            .eq("property_id", property_id)
            .eq("property_type", normalized_work_type)
            .limit(1)
            .execute()
        )

        payload = {
            "property_id": property_id,
            "property_name": property_row.get("property_name") or "",
            "property_type": normalized_work_type,
            "rate": max(0, _to_int(unit_price, 0)),
            "note": "",
            "is_active": bool(is_active),
        }

        if existing.data:
            res = (
                supabase.table("property_type_piece_rates")
                .update(payload)
                .eq("id", existing.data[0]["id"])
                .execute()
            )
        else:
            res = supabase.table("property_type_piece_rates").insert(payload).execute()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"upsert_property_type_piece_rate_compat failed: property_id={property_id} {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="物件タイプ単価の保存に失敗しました。")

    if not res.data:
        raise HTTPException(status_code=500, detail="物件タイプ単価の保存に失敗しました。")
    return res.data[0]
