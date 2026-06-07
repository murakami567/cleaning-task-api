import re
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.db import supabase
from app.logger import get_logger
from app.services.auth_service import require_admin_or_leader
from app.services.jinjer_service import fetch_work_schedules

router = APIRouter(prefix="/jinjer", tags=["jinjer"])
logger = get_logger(__name__)


MONTH_PATTERN = re.compile(r"^\d{4}-\d{2}$")


def _normalize_time(t: str | None) -> str | None:
    """Jinjer は HH:MM:SS で返してくるので HH:MM に丸める。"""
    if not t:
        return None
    s = str(t).strip()
    if not s:
        return None
    return s[:5]


@router.post("/shifts/sync")
def sync_jinjer_shifts(
    month: str = Body(..., embed=True),
    current_user: dict = Depends(require_admin_or_leader),
):
    """
    Jinjer から指定月のシフトを取得して shift_entries に反映する。

    マッピング:
      - employee_id  →  staff_members.staff_code で突合
      - date         →  shift_days.shift_date (無ければ作成)
      - work_schedule.start  →  shift_entries.start_time
      - work_schedule.end    →  shift_entries.end_time
      - status               →  '出勤' (Jinjer に予定があるものは出勤扱い)

    既存の shift_entries は upsert で更新。assigned_area / note は
    Jinjer 由来に上書きせず保持する（手動で入れた情報を消さない）。
    """
    if not MONTH_PATTERN.match(month or ""):
        raise HTTPException(status_code=400, detail="month は YYYY-MM 形式で指定してください。")

    # 1. Jinjer からシフト一覧を取得
    items = fetch_work_schedules(month)

    if not items:
        return {
            "month": month,
            "fetched": 0,
            "matched_staff": 0,
            "saved": 0,
            "skipped_no_staff": [],
            "errors": [],
        }

    # 2. staff_code → staff_id のマップを作成
    try:
        staff_res = (
            supabase.table("staff_members")
            .select("id, staff_code")
            .eq("is_active", True)
            .limit(10000)
            .execute()
        )
    except Exception as e:
        logger.error(f"sync_jinjer_shifts staff lookup failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="スタッフ取得に失敗しました。")

    code_to_id: dict[str, str] = {}
    for row in staff_res.data or []:
        code = row.get("staff_code")
        if code:
            code_to_id[str(code).strip()] = row.get("id")

    # 3. 取得期間内に必要となる日付の shift_days を一気に取得 / 作成
    needed_dates: set[str] = set()
    for it in items:
        if it["employee_id"] in code_to_id and it.get("date"):
            needed_dates.add(it["date"])

    try:
        day_res = (
            supabase.table("shift_days")
            .select("id, shift_date")
            .in_("shift_date", list(needed_dates))
            .execute()
        ) if needed_dates else None
    except Exception as e:
        logger.error(f"sync_jinjer_shifts shift_days lookup failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="shift_days 取得に失敗しました。")

    date_to_day_id: dict[str, str] = {}
    for row in (day_res.data if day_res else []) or []:
        date_to_day_id[row["shift_date"]] = row["id"]

    # 不足分は作成
    for d in sorted(needed_dates):
        if d in date_to_day_id:
            continue
        try:
            created = (
                supabase.table("shift_days")
                .insert({"shift_date": d, "note": ""})
                .execute()
            )
            if created.data:
                date_to_day_id[d] = created.data[0]["id"]
        except Exception as e:
            logger.error(f"sync_jinjer_shifts shift_day create failed: date={d} {e}", exc_info=True)

    # 4. 既存の shift_entries を (date, staff_id) でマップ化（assigned_area / note 保持用）
    try:
        ent_res = (
            supabase.table("shift_entries")
            .select("id, shift_day_id, staff_id, assigned_area, note, status")
            .in_("shift_day_id", list(date_to_day_id.values()))
            .limit(50000)
            .execute()
        ) if date_to_day_id else None
    except Exception as e:
        logger.error(f"sync_jinjer_shifts entries lookup failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="shift_entries 取得に失敗しました。")

    existing: dict[tuple[str, str], dict[str, Any]] = {}
    for row in (ent_res.data if ent_res else []) or []:
        key = (row["shift_day_id"], row["staff_id"])
        existing[key] = row

    # 5. アイテムごとに upsert
    saved = 0
    skipped_no_staff: list[str] = []
    errors: list[dict[str, Any]] = []
    seen_no_staff: set[str] = set()

    for it in items:
        employee_id = it["employee_id"]
        staff_id = code_to_id.get(employee_id)
        if not staff_id:
            if employee_id not in seen_no_staff:
                seen_no_staff.add(employee_id)
                skipped_no_staff.append(employee_id)
            continue

        shift_day_id = date_to_day_id.get(it["date"])
        if not shift_day_id:
            continue

        start_time = _normalize_time(it.get("start"))
        end_time = _normalize_time(it.get("end"))

        # Jinjer は予定としてレコードを返してくるので、予定 = 出勤扱い
        # 既存のステータスが 欠勤/遅刻 等で手動変更されていた場合は上書きしない。
        prev = existing.get((shift_day_id, staff_id)) or {}
        prev_status = prev.get("status")
        if prev_status in ("欠勤", "遅刻"):
            new_status = prev_status  # 手動の欠勤/遅刻を保持
        else:
            new_status = "出勤"

        payload = {
            "shift_day_id": shift_day_id,
            "staff_id": staff_id,
            "status": new_status,
            "start_time": start_time,
            "end_time": end_time,
            # assigned_area / note は既存値を維持
            "assigned_area": prev.get("assigned_area") or "",
            "note": prev.get("note") or "",
        }

        try:
            if prev.get("id"):
                supabase.table("shift_entries").update(payload).eq("id", prev["id"]).execute()
            else:
                supabase.table("shift_entries").insert(payload).execute()
            saved += 1
        except Exception as e:
            errors.append({
                "employee_id": employee_id,
                "date": it["date"],
                "error": str(e)[:200],
            })
            if len(errors) > 50:
                # ログにも残してレスポンスサイズが膨れすぎないように
                logger.error(f"sync_jinjer_shifts too many errors")
                break

    logger.info(
        f"sync_jinjer_shifts: month={month} fetched={len(items)}"
        f" matched_staff={len(code_to_id)} saved={saved}"
        f" skipped_no_staff={len(skipped_no_staff)} errors={len(errors)}"
    )

    return {
        "month": month,
        "fetched": len(items),
        "matched_staff": len(code_to_id),
        "saved": saved,
        "skipped_no_staff": skipped_no_staff[:50],
        "errors": errors[:50],
    }
