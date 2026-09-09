from fastapi import APIRouter, Body, Depends, HTTPException

from app.logger import get_logger
from app.services.auth_service import require_admin_write
from app.services.contractor_assignment_service import sync_existing_contractor_tasks

router = APIRouter(prefix="/contractor-assignment", tags=["contractor-assignment"])
logger = get_logger(__name__)


@router.post("/sync")
def sync_contractor_assignment(
    staff_id: str | None = Body(None, embed=True),
    current_user: dict = Depends(require_admin_write),
):
    try:
        return sync_existing_contractor_tasks(staff_id)
    except Exception as e:
        logger.error(f"contractor assignment sync failed: staff_id={staff_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="委託業者の固定割当反映に失敗しました。")
