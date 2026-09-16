import os

import requests

from app.db import supabase
from app.logger import get_logger

logger = get_logger(__name__)


def _settings() -> tuple[str, str]:
    base_url = os.getenv("FACILITY_MANAGEMENT_URL", "https://facility.gusk.jp").rstrip("/")
    secret = os.getenv("MASTER_SYNC_SECRET", "").strip()
    if not secret:
        raise RuntimeError("MASTER_SYNC_SECRET is not configured")
    return base_url, secret


def _post(path: str, payload: dict, reason: str) -> bool:
    try:
        base_url, secret = _settings()
        response = requests.post(
            f"{base_url}{path}",
            json=payload,
            headers={"Authorization": f"Bearer {secret}"},
            timeout=15,
        )
        response.raise_for_status()
        body = response.json()
        if not body.get("ok"):
            raise RuntimeError(str(body.get("error") or "Facility master sync failed"))
        logger.info("facility sync success: reason=%s path=%s", reason, path)
        return True
    except Exception as exc:
        logger.error("facility sync failed: reason=%s path=%s error=%s", reason, path, exc, exc_info=True)
        return False


def build_master_payload() -> dict:
    properties_res = supabase.table("properties").select(
        "id,property_code,property_name,address,is_active"
    ).order("sort_order").execute()
    rooms_res = supabase.table("rooms").select(
        "id,property_id,room_name,room_code,room_sort_order,is_active"
    ).order("room_sort_order").execute()
    return {"properties": properties_res.data or [], "rooms": rooms_res.data or []}


def sync_facility_master(reason: str = "reconciliation") -> bool:
    """Full reconciliation sync. Use for initial/periodic repair, not normal edits."""
    return _post("/api/master-sync", build_master_payload(), reason)


def sync_facility_property(property_id: str, reason: str = "property_changed") -> bool:
    try:
        res = supabase.table("properties").select(
            "id,property_code,property_name,address,is_active"
        ).eq("id", property_id).limit(1).execute()
        if not res.data:
            logger.warning("facility property delta skipped: property not found id=%s", property_id)
            return False
        return _post("/api/master-sync/delta", {"entity_type": "property", "property": res.data[0]}, reason)
    except Exception as exc:
        logger.error("facility property delta build failed: id=%s error=%s", property_id, exc, exc_info=True)
        return False


def sync_facility_room(room_id: str, reason: str = "room_changed") -> bool:
    try:
        res = supabase.table("rooms").select(
            "id,property_id,room_name,room_code,room_sort_order,is_active"
        ).eq("id", room_id).limit(1).execute()
        if not res.data:
            return _post("/api/master-sync/delta", {"entity_type": "room_delete", "room_id": room_id}, reason)
        room = res.data[0]
        prop_res = supabase.table("properties").select(
            "id,property_code,property_name,address,is_active"
        ).eq("id", room["property_id"]).limit(1).execute()
        if not prop_res.data:
            logger.warning("facility room delta skipped: parent property not found room=%s", room_id)
            return False
        return _post(
            "/api/master-sync/delta",
            {"entity_type": "room", "property": prop_res.data[0], "room": room},
            reason,
        )
    except Exception as exc:
        logger.error("facility room delta build failed: id=%s error=%s", room_id, exc, exc_info=True)
        return False
