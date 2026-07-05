from fastapi import APIRouter, Body, Depends, HTTPException

from app.db import supabase
from app.logger import get_logger
from app.services.auth_service import require_admin_write

router = APIRouter(tags=["properties"])
logger = get_logger(__name__)


ASSIGNMENT_MODES = ["solo", "shared", "both"]


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
        logger.error(f"update_property failed: property_id={property_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="property update failed")

    return {"ok": True, "property_id": property_id, "updated": payload, "data": res.data}
