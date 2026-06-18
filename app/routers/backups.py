import csv
import io
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException

from app.db import supabase
from app.logger import get_logger

router = APIRouter(prefix="/backups", tags=["backups"])
logger = get_logger(__name__)

BACKUP_TABLES = [
    "staff_members",
    "properties",
    "rooms",
    "cleaning_tasks",
    "shift_days",
    "shift_entries",
    "attendance_logs",
    "work_logs",
    "facilities",
    "mate_cartes",
    "portal_messages",
    "non_cleaning_tasks",
]

PAGE_SIZE = 1000
DEFAULT_RETENTION_DAYS = 30


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_jst() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=9)).date().isoformat()


def _expected_key() -> str | None:
    return os.getenv("BACKUP_API_KEY") or os.getenv("CRON_SECRET")


def _require_backup_key(x_backup_key: str | None, x_cron_key: str | None) -> None:
    expected = _expected_key()
    provided = x_backup_key or x_cron_key
    if not expected:
        raise HTTPException(status_code=500, detail="BACKUP_API_KEY or CRON_SECRET is not configured")
    if not provided or provided != expected:
        raise HTTPException(status_code=401, detail="Invalid backup key")


def _fetch_all_rows(table_name: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start = 0
    while True:
        end = start + PAGE_SIZE - 1
        res = supabase.table(table_name).select("*").range(start, end).execute()
        batch = res.data or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return rows


def _json_safe(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _rows_to_csv(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""

    fieldnames: list[str] = []
    seen = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _json_safe(row.get(key)) for key in fieldnames})
    return output.getvalue()


def _delete_old_backups(retention_days: int) -> int:
    if retention_days <= 0:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    try:
        res = supabase.table("backups").delete().lt("created_at", cutoff).execute()
        return len(res.data or [])
    except Exception as e:
        logger.warning(f"delete old backups skipped: {e}")
        return 0


@router.post("/run")
def run_backup(
    x_backup_key: str | None = Header(default=None, alias="X-BACKUP-KEY"),
    x_cron_key: str | None = Header(default=None, alias="X-CRON-KEY"),
    retention_days: int = DEFAULT_RETENTION_DAYS,
):
    _require_backup_key(x_backup_key, x_cron_key)

    backup_id = str(uuid4())
    backup_date = _today_jst()
    created_at = _now_iso()
    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for table_name in BACKUP_TABLES:
        try:
            rows = _fetch_all_rows(table_name)
            json_data = rows
            csv_data = _rows_to_csv(rows)
            payload = {
                "backup_id": backup_id,
                "backup_date": backup_date,
                "table_name": table_name,
                "row_count": len(rows),
                "json_data": json_data,
                "csv_data": csv_data,
                "created_at": created_at,
            }
            supabase.table("backups").insert(payload).execute()
            results.append({
                "table_name": table_name,
                "row_count": len(rows),
                "json_bytes": len(json.dumps(json_data, ensure_ascii=False)),
                "csv_bytes": len(csv_data.encode("utf-8")),
            })
        except Exception as e:
            logger.error(f"backup failed: table={table_name} error={e}", exc_info=True)
            errors.append({"table_name": table_name, "error": str(e)})

    deleted_old = _delete_old_backups(retention_days)
    ok = len(errors) == 0
    logger.info(
        f"backup completed: backup_id={backup_id} ok={ok} tables={len(results)} errors={len(errors)} deleted_old={deleted_old}"
    )
    return {
        "ok": ok,
        "backup_id": backup_id,
        "backup_date": backup_date,
        "created_at": created_at,
        "tables": results,
        "errors": errors,
        "deleted_old": deleted_old,
        "retention_days": retention_days,
    }


@router.get("/latest")
def get_latest_backups(
    x_backup_key: str | None = Header(default=None, alias="X-BACKUP-KEY"),
    x_cron_key: str | None = Header(default=None, alias="X-CRON-KEY"),
):
    _require_backup_key(x_backup_key, x_cron_key)
    try:
        res = (
            supabase.table("backups")
            .select("backup_id, backup_date, table_name, row_count, created_at")
            .order("created_at", desc=True)
            .limit(100)
            .execute()
        )
    except Exception as e:
        logger.error(f"get latest backups failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="バックアップ一覧の取得に失敗しました。")
    return res.data or []
