from fastapi import APIRouter, Body, Header, HTTPException
import os

from app.logger import get_logger
from app.services.beds24_service import beds24_csv_sync_service
from app.services.contractor_assignment_service import sync_existing_contractor_tasks

router = APIRouter(tags=["beds24"])
logger = get_logger(__name__)

SYNC_API_KEY = os.getenv("SYNC_API_KEY", "")


@router.post("/beds24/csv/sync")
def beds24_csv_sync(
    from_date: str | None = Body(default=None),
    to_date: str | None = Body(default=None),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
):
    if SYNC_API_KEY and x_api_key != SYNC_API_KEY:
        logger.warning("beds24 sync: unauthorized request")
        raise HTTPException(
            status_code=401,
            detail={
                "message": "unauthorized",
                "received": x_api_key,
                "configured": bool(SYNC_API_KEY),
            },
        )

    logger.info(f"beds24 sync started: from_date={from_date} to_date={to_date}")
    try:
        result = beds24_csv_sync_service(from_date=from_date, to_date=to_date)
        try:
            contractor_sync = sync_existing_contractor_tasks()
            result["contractor_assignment_sync"] = contractor_sync
        except Exception as sync_error:
            logger.error(f"contractor sync after beds24 failed: {sync_error}", exc_info=True)
            result["contractor_assignment_sync"] = {"ok": False, "error": str(sync_error)}
        logger.info("beds24 sync completed")
        return result
    except Exception as e:
        logger.error(f"beds24 sync failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Beds24同期に失敗しました: {str(e)}")
