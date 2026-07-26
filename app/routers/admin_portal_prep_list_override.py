from fastapi import APIRouter, Depends

from app.routers.admin_portal import get_prep_list as get_prep_list_payload
from app.services.auth_service import require_admin_or_leader

router = APIRouter(prefix="/api/admin-portal", tags=["admin-portal"])


@router.get("/prep-list")
def get_prep_list_array(current_user: dict = Depends(require_admin_or_leader)):
    """Return the prep list as an array for the property management screen."""
    payload = get_prep_list_payload(current_user=current_user)
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        items = payload.get("items")
        return items if isinstance(items, list) else []
    return []
