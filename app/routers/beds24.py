from fastapi import APIRouter, Body, Header, HTTPException
import os

from app.services.beds24_service import beds24_csv_sync_service

router = APIRouter(tags=["beds24"])

SYNC_API_KEY = os.getenv("SYNC_API_KEY", "")


@router.post("/beds24/csv/sync")
def beds24_csv_sync(
    from_date: str | None = Body(default=None),
    to_date: str | None = Body(default=None),
    x_api_key: str | None = Header(default=None),
):

    if SYNC_API_KEY and x_api_key != SYNC_API_KEY:
        raise HTTPException(status_code=401, detail="unauthorized")

    return beds24_csv_sync_service(
        from_date=from_date,
        to_date=to_date
    )
