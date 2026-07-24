import re
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.db import supabase
from app.logger import get_logger
from app.services.auth_service import require_admin_write

router = APIRouter(tags=["properties"])
logger = get_logger(__name__)


ASSIGNMENT_MODES = ["solo", "shared", "both"]
COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")


def _normalize_max_assignable_count(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        n = int(value)
    except Exception:
        raise HTTPException(status_code=400, detail="max_assignable_count must be a number")
    if n < 0:
        raise HTTPException(status_code=400, detail="max_assignable_count must be 0 or more")
    return n


def _normalize_assignment_mode(value: str | None) -> str:
    if value in [None, ""]:
        return "solo"
    if value not in ASSIGNMENT_MODES:
        raise HTTPException(status_code=400, detail="assignment_mode must be solo, shared or both")
    return value


def _normalize_cleaning_point(value) -> int:
    if value is None or value == "":
        return 60
    try:
        n = int(value)
    except Exception:
        raise HTTPException(status_code=400, detail="cleaning_point must be a number")
    if n <= 0:
        raise HTTPException(status_code=400, detail="cleaning_point must be greater than 0")
    return n


def _normalize_task_color(value: str | None) -> str:
    color = str(value or "#ffffff").strip()
    if not color:
        return "#ffffff"
    if not COLOR_PATTERN.match(color):
        raise HTTPException(status_code=400, detail="task_color must be #RRGGBB format")
    return color.lower()


def _text(value) -> str:
    return str(value or "").strip()


def _normalize_reorder_items(items: Any) -> list[dict[str, int | str]]:
    if isinstance(items, dict):
        items = items.get("items") or items.get("properties") or items.get("orders") or []
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="items must be a list")

    normalized = []
    seen_ids = set()
    for index, row in enumerate(items):
        if not isinstance(row, dict):
            raise HTTPException(status_code=400, detail="each item must be an object")
        property_id = str(row.get("id") or row.get("property_id") or "").strip()
        if not property_id:
            raise HTTPException(status_code=400, detail="property_id is required")
        if property_id in seen_ids:
            continue
        seen_ids.add(property_id)
        sort_order = row.get("sort_order")
        try:
            sort_order = int(sort_order if sort_order is not None else index + 1)
        except Exception:
            sort_order = index + 1
        normalized.append({"property_id": property_id, "sort_order": sort_order})
    return normalized


@router.post("/properties/create")
def create_property(
    property_code: str = Body(...),
    property_name: str = Body(...),
    normalized_name: str | None = Body(None),
    sort_order: int | None = Body(999),
    is_active: bool = Body(True),
    max_assignable_count: int | None = Body(None),
    assignment_mode: str | None = Body("solo"),
    cleaning_point: int | None = Body(60),
    task_color: str | None = Body("#ffffff"),
    address: str | None = Body(None),
    google_maps_url: str | None = Body(None),
    current_user: dict = Depends(require_admin_write),
):
    payload = {
        "property_code": property_code.strip(),
        "property_name": property_name.strip(),
        "normalized_name": (normalized_name or property_name).strip(),
        "sort_order": sort_order if sort_order is not None else 999,
        "is_active": is_active,
        "max_assignable_count": _normalize_max_assignable_count(max_assignable_count),
        "assignment_mode": _normalize_assignment_mode(assignment_mode),
        "cleaning_point": _normalize_cleaning_point(cleaning_point),
        "task_color": _normalize_task_color(task_color),
        "address": _text(address),
        "google_maps_url": _text(google_maps_url),
    }

    if not payload["property_code"]:
        raise HTTPException(status_code=400, detail="property_code is required")
    if not payload["property_name"]:
        raise HTTPException(status_code=400, detail="property_name is required")

    try:
        res = supabase.table("properties").insert(payload).execute()
    except Exception as e:
        logger.error(f"create_property failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="property creation failed")

    if not res.data:
        raise HTTPException(status_code=500, detail="property creation failed")
    return res.data[0]


@router.post("/properties/update")
def update_property(
    property_id: str = Body(...),
    property_code: str | None = Body(None),
    property_name: str | None = Body(None),
    normalized_name: str | None = Body(None),
    sort_order: int | None = Body(None),
    is_active: bool | None = Body(None),
    max_assignable_count: int | None = Body(None),
    assignment_mode: str | None = Body(None),
    cleaning_point: int | None = Body(None),
    task_color: str | None = Body(None),
    address: str | None = Body(None),
    google_maps_url: str | None = Body(None),
    current_user: dict = Depends(require_admin_write),
):
    payload = {}

    if property_code is not None:
        payload["property_code"] = property_code.strip()
    if property_name is not None:
        payload["property_name"] = property_name.strip()
    if normalized_name is not None:
        payload["normalized_name"] = normalized_name.strip()
    if sort_order is not None:
        payload["sort_order"] = sort_order
    if is_active is not None:
        payload["is_active"] = is_active
    if max_assignable_count is not None:
        payload["max_assignable_count"] = _normalize_max_assignable_count(max_assignable_count)
    if assignment_mode is not None:
        payload["assignment_mode"] = _normalize_assignment_mode(assignment_mode)
    if cleaning_point is not None:
        payload["cleaning_point"] = _normalize_cleaning_point(cleaning_point)
    if task_color is not None:
        payload["task_color"] = _normalize_task_color(task_color)
    if address is not None:
        payload["address"] = _text(address)
    if google_maps_url is not None:
        payload["google_maps_url"] = _text(google_maps_url)

    if not property_id:
        raise HTTPException(status_code=400, detail="property_id is required")
    if not payload:
        raise HTTPException(status_code=400, detail="no update fields")

    try:
        res = (
            supabase.table("properties")
            .update(payload)
            .eq("id", property_id)
            .execute()
        )
    except Exception as e:
        logger.error(f"update_property failed: property_id={property_id} payload={payload} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"property update failed: {str(e)}")

    return {"ok": True, "property_id": property_id, "updated": payload, "data": res.data}


@router.post("/properties/reorder")
def reorder_properties(
    body: Any = Body(...),
    current_user: dict = Depends(require_admin_write),
):
    items = _normalize_reorder_items(body)
    if not items:
        raise HTTPException(status_code=400, detail="items is empty")

    updated = []
    try:
        for item in items:
            res = (
                supabase.table("properties")
                .update({"sort_order": item["sort_order"]})
                .eq("id", item["property_id"])
                .execute()
            )
            updated.extend(res.data or [])
    except Exception as e:
        logger.error(f"reorder_properties failed: items={items} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"property reorder failed: {str(e)}")

    return {"ok": True, "count": len(items), "updated": updated}
