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


def build_master_payload() -> dict:
    properties_res = (
        supabase.table("properties")
        .select("id,property_code,property_name,address,is_active")
        .order("sort_order")
        .execute()
    )
    rooms_res = (
        supabase.table("rooms")
        .select("id,property_id,room_name,room_code,room_sort_order,is_active")
        .order("room_sort_order")
        .execute()
    )
    return {
        "properties": properties_res.data or [],
        "rooms": rooms_res.data or [],
    }


def sync_facility_master(reason: str = "master_changed") -> bool:
    """Best-effort full master sync. Cleaning Task writes must not fail if Facility is unavailable."""
    try:
        base_url, secret = _settings()
        payload = build_master_payload()
        response = requests.post(
            f"{base_url}/api/master-sync",
            json=payload,
            headers={"Authorization": f"Bearer {secret}"},
            timeout=20,
        )
        response.raise_for_status()
        body = response.json()
        if not body.get("ok"):
            raise RuntimeError(str(body.get("error") or "Facility master sync failed"))
        logger.info(
            "facility master sync success: reason=%s properties=%s rooms=%s",
            reason,
            len(payload["properties"]),
            len(payload["rooms"]),
        )
        return True
    except Exception as exc:
        logger.error("facility master sync failed: reason=%s error=%s", reason, exc, exc_info=True)
        return False
