from fastapi import APIRouter, Body, Depends, HTTPException

from app.db import supabase
from app.logger import get_logger
from app.services.auth_service import require_admin_write

router = APIRouter(tags=["rooms"])
logger = get_logger(__name__)


def _score(value) -> int:
    try:
        n = int(value)
    except Exception:
        return 60
    return n if n > 0 else 60


def _int_or_default(value, default: int) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


def _text(value) -> str:
    return str(value or "").strip()


@router.post("/rooms/create")
def create_room(
    property_id: str = Body(...),
    room_name: str = Body(...),
    room_code: str | None = Body(None),
    room_key: str | None = Body(None),
    normalized_room_key: str | None = Body(None),
    capacity: int | None = Body(1),
    room_sort_order: int | None = Body(999),
    is_active: bool = Body(True),
    prep_d: int | None = Body(0),
    prep_s: int | None = Body(0),
    prep_spare_s: int | None = Body(0),
    prep_ta: int | None = Body(0),
    cleaning_score: int | None = Body(None),
    current_user: dict = Depends(require_admin_write),
):
    property_id = _text(property_id)
    room_name = _text(room_name)
    if not property_id:
        raise HTTPException(status_code=400, detail="property_id is required")
    if not room_name:
        raise HTTPException(status_code=400, detail="room_name is required")

    code = _text(room_code) or room_name
    key = _text(room_key) or room_name
    normalized_key = _text(normalized_room_key) or key

    payload = {
        "property_id": property_id,
        "room_name": room_name,
        "room_code": code,
        "room_key": key,
        "normalized_room_key": normalized_key,
        "capacity": _int_or_default(capacity, 1),
        "room_sort_order": _int_or_default(room_sort_order, 999),
        "is_active": is_active,
        "prep_d": _int_or_default(prep_d, 0),
        "prep_s": _int_or_default(prep_s, 0),
        "prep_spare_s": _int_or_default(prep_spare_s, 0),
        "prep_ta": _int_or_default(prep_ta, 0),
    }
    if cleaning_score is not None:
        payload["cleaning_score"] = _score(cleaning_score)

    try:
        res = supabase.table("rooms").insert(payload).execute()
    except Exception as e:
        logger.error(f"create_room failed: property_id={property_id} room_name={room_name} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="room create failed")

    if not res.data:
        raise HTTPException(status_code=500, detail="room create failed")
    return res.data[0]


@router.post("/rooms/bulk-create")
def bulk_create_rooms(
    property_id: str = Body(...),
    room_names: list[str] = Body(...),
    default_capacity: int | None = Body(1),
    start_sort_order: int | None = Body(1),
    current_user: dict = Depends(require_admin_write),
):
    property_id = _text(property_id)
    names = [_text(name) for name in room_names or [] if _text(name)]
    if not property_id:
        raise HTTPException(status_code=400, detail="property_id is required")
    if not names:
        raise HTTPException(status_code=400, detail="room_names is required")

    try:
        prop_res = (
            supabase.table("properties")
            .select("property_name")
            .eq("id", property_id)
            .limit(1)
            .execute()
        )
    except Exception as e:
        logger.error(f"bulk_create_rooms property lookup failed: property_id={property_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="property lookup failed")

    property_name = (prop_res.data or [{}])[0].get("property_name") if prop_res.data else ""
    base_order = _int_or_default(start_sort_order, 1)
    capacity = _int_or_default(default_capacity, 1)

    rows = []
    seen: set[str] = set()
    for index, name in enumerate(names):
        if name in seen:
            continue
        seen.add(name)
        room_key = f"{property_name}{name}" if property_name else name
        rows.append({
            "property_id": property_id,
            "room_name": name,
            "room_code": name,
            "room_key": room_key,
            "normalized_room_key": room_key,
            "capacity": capacity,
            "room_sort_order": base_order + index,
            "is_active": True,
            "prep_d": 0,
            "prep_s": 0,
            "prep_spare_s": 0,
            "prep_ta": 0,
        })

    try:
        res = supabase.table("rooms").insert(rows).execute()
    except Exception as e:
        logger.error(f"bulk_create_rooms failed: property_id={property_id} count={len(rows)} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="rooms bulk create failed")

    return {"ok": True, "count": len(res.data or []), "data": res.data or []}


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
    current_user: dict = Depends(require_admin_write),
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


@router.post("/rooms/delete")
def delete_room(room_id: str = Body(...), current_user: dict = Depends(require_admin_write)):
    if not room_id:
        raise HTTPException(status_code=400, detail="room_id is required")
    try:
        res = supabase.table("rooms").delete().eq("id", room_id).execute()
    except Exception as e:
        logger.error(f"delete_room failed: room_id={room_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="room delete failed")
    return {"ok": True, "room_id": room_id, "data": res.data or []}
