from fastapi import APIRouter, Body, HTTPException

from app.db import supabase
from app.logger import get_logger

router = APIRouter(tags=["rooms"])
logger = get_logger(__name__)


def _score(value) -> int:
    try:
        n = int(value)
    except Exception:
        return 60
    return n if n > 0 else 60


@router.post("/rooms/update")
def update_room(
    room_id: str = Body(...),
    property_id: str | None = Body(None),
    room_name: str | None = Body(None),
    room_code: str | None = Body(None),
    room_key: str | None = Body(None),
    normalized_room_key: str | None = Body(None),
    capacity: int | None = Body(None),
    room_sort_order: int | None = Body(None),
    is_active: bool | None = Body(None),
    prep_d: int | None = Body(None),
    prep_s: int | None = Body(None),
    prep_spare_s: int | None = Body(None),
    prep_ta: int | None = Body(None),
    cleaning_score: int | None = Body(None),
):
    payload = {}
    if property_id is not None:
        payload["property_id"] = property_id
    if room_name is not None:
        payload["room_name"] = room_name.strip()
    if room_code is not None:
        payload["room_code"] = room_code.strip()
    if room_key is not None:
        payload["room_key"] = room_key.strip()
    if normalized_room_key is not None:
        payload["normalized_room_key"] = normalized_room_key.strip()
    if capacity is not None:
        payload["capacity"] = capacity
    if room_sort_order is not None:
        payload["room_sort_order"] = room_sort_order
    if is_active is not None:
        payload["is_active"] = is_active
    if prep_d is not None:
        payload["prep_d"] = prep_d
    if prep_s is not None:
        payload["prep_s"] = prep_s
    if prep_spare_s is not None:
        payload["prep_spare_s"] = prep_spare_s
    if prep_ta is not None:
        payload["prep_ta"] = prep_ta
    if cleaning_score is not None:
        payload["cleaning_score"] = _score(cleaning_score)

    if not room_id:
        raise HTTPException(status_code=400, detail="room_id is required")
    if not payload:
        raise HTTPException(status_code=400, detail="no update fields")

    try:
        res = supabase.table("rooms").update(payload).eq("id", room_id).execute()
    except Exception as e:
        logger.error(f"update_room failed: room_id={room_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="room update failed")
    return {"ok": True, "room_id": room_id, "updated": payload, "data": res.data}
