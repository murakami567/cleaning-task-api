from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header

from app.routers.jinjer import _sync_attendances_for_range, _verify_cron_key
from app.services.auth_service import require_admin_or_leader

router = APIRouter(prefix="/jinjer", tags=["jinjer-attendance-override"])


def _today_jst_iso() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=9)).date().isoformat()


@router.post("/attendances/sync-today")
def sync_jinjer_attendances_today_override(current_user: dict = Depends(require_admin_or_leader)):
    today = _today_jst_iso()
    return _sync_attendances_for_range(today, today)


@router.post("/attendances/cron-sync")
def cron_sync_jinjer_attendances_override(
    x_cron_key: str | None = Header(None, alias="X-CRON-KEY"),
):
    _verify_cron_key(x_cron_key)
    today = _today_jst_iso()
    return _sync_attendances_for_range(today, today)
