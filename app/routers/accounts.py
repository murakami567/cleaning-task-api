from fastapi import APIRouter, Body, HTTPException

from app.db import supabase
from app.logger import get_logger

router = APIRouter(tags=["accounts"])
logger = get_logger(__name__)


@router.get("/staffs")
def get_staffs():
    try:
        res = (
            supabase.table("staff_members")
            .select("*")
            .order("sort_order")
            .order("staff_name")
            .execute()
        )
    except Exception as e:
        logger.error(f"accounts get_staffs failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="スタッフ情報の取得に失敗しました。")

    logger.info(f"accounts get_staffs: count={len(res.data or [])}")
    return res.data or []


@router.post("/staffs/upsert")
def upsert_staff(
    staff_id: str | None = Body(None),
    staff_code: str = Body(...),
    staff_name: str = Body(...),
    role: str = Body("staff"),
    sort_order: int = Body(999),
    is_active: bool = Body(True),
    note: str = Body(""),
    password: str | None = Body(None),
    area: str | None = Body(None),
    available_property_ids: list[str] | None = Body(None),
    unchecked_property_ids: list[str] | None = Body(None),
    lineworks_channel_id: str | None = Body(None),
    daily_capacity_point: int | None = Body(None),
    solo_enabled: bool | None = Body(None),
    shared_enabled: bool | None = Body(None),
):
    # チェック解除済み物件が最上位。
    # 同じ物件が両方に入った場合は、チェック解除済みを優先して通常対応から除外する。
    priority_ids = list(dict.fromkeys(unchecked_property_ids or []))
    priority_set = set(priority_ids)
    normal_ids = [
        property_id
        for property_id in list(dict.fromkeys(available_property_ids or []))
        if property_id not in priority_set
    ]

    payload = {
        "staff_code": staff_code,
        "staff_name": staff_name,
        "role": role,
        "sort_order": sort_order,
        "is_active": is_active,
        "note": note,
        "available_property_ids": normal_ids,
        "unchecked_property_ids": priority_ids,
    }

    if daily_capacity_point is not None:
        payload["daily_capacity_point"] = max(0, int(daily_capacity_point))
    if solo_enabled is not None:
        payload["solo_enabled"] = solo_enabled
    if shared_enabled is not None:
        payload["shared_enabled"] = shared_enabled
    if password is not None:
        payload["password"] = password
    if area is not None:
        payload["area"] = area
    if lineworks_channel_id is not None:
        payload["lineworks_channel_id"] = lineworks_channel_id

    try:
        if staff_id:
            res = (
                supabase.table("staff_members")
                .update(payload)
                .eq("id", staff_id)
                .execute()
            )
        else:
            res = supabase.table("staff_members").insert(payload).execute()
    except Exception as e:
        logger.error(f"accounts upsert_staff failed: staff_id={staff_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="staff save failed")

    if not res.data:
        raise HTTPException(status_code=500, detail="staff save failed")

    logger.info(f"accounts upsert_staff: staff_id={res.data[0].get('id')}")
    return res.data[0]
