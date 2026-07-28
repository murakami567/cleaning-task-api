import csv
import io
import os
from collections import defaultdict
from datetime import date, datetime, timedelta

import requests
from fastapi import HTTPException

from app.db import supabase


BEDS24_CSV_URL = os.getenv("BEDS24_CSV_URL", "https://www.beds24.com/api/csv/getbookingscsv")
BEDS24_CSV_USERNAME = os.getenv("BEDS24_CSV_USERNAME", "")
BEDS24_CSV_PASSWORD = os.getenv("BEDS24_CSV_PASSWORD", "")


PROPERTY_ORDER = [
    "FFFホテル", "やなぎ橋", "住吉", "アクシオン", "ブランシェ", "ウィングス", "美野島",
    "玉井", "ウーブル博多", "いそのビル", "ジェン", "ルッシェ", "東光", "グランデエス",
    "エスコート", "アトラス", "薬院", "ロイズ", "ピット", "県庁前",
    "西中洲", "冷泉", "駅前モダン", "比恵モダン", "浄水",
]

CARRY_OVER_STATUS = "持越"
CARRY_OVER_RESET_STATUS = "未着手"

# Beds24 同期で上書きしてはいけない、現場運用側の値。
OPERATIONAL_FIELDS = (
    "task_date",
    "status",
    "note",
    "assigned_staff_ids",
    "assigned_staff_names",
    "assigned_staff_id",
    "assigned_staff_name",
    "checker_id",
    "checker_name",
    "assignment_locked",
    "cleaning_started_at",
)


def format_date_string(dt: date) -> str:
    return dt.strftime("%Y-%m-%d")


def parse_csv_text(csv_text: str):
    reader = csv.reader(io.StringIO(csv_text))
    return list(reader)


def safe_get(row, idx):
    if idx < 0 or idx >= len(row):
        return ""
    return row[idx]


def parse_beds24_date(date_str: str | None):
    if not date_str:
        return None

    s = str(date_str).strip()
    if not s:
        return None

    for fmt in ("%Y-%m-%d", "%d %b %Y", "%d %b %Y %H:%M"):
        try:
            if fmt == "%Y-%m-%d":
                return datetime.fromisoformat(s[:10]).date().isoformat()
            return datetime.strptime(s.replace(",", ""), fmt).date().isoformat()
        except Exception:
            pass

    return None


def find_target_columns(header_row):
    target_columns = {
        "Title": -1,
        "Property": -1,
        "Unit": -1,
        "FirstNight": -1,
        "Check Out": -1,
        "Price": -1,
        "Status": -1,
        "Referer": -1,
        "Adult": -1,
        "Child": -1,
        "Time Entered": -1,
        "Full Name": -1,
        "Ref": -1,
    }

    for i, h in enumerate(header_row):
        header = str(h).strip()
        if header in target_columns:
            target_columns[header] = i

    required = ["Title", "Property", "Unit", "FirstNight", "Check Out", "Status", "Ref"]
    missing = [k for k in required if target_columns[k] == -1]

    if missing:
        raise HTTPException(
            status_code=500,
            detail=f"required csv columns missing: {', '.join(missing)}",
        )

    return target_columns


def normalize_property_name(property_raw: str) -> str:
    raw = str(property_raw or "").strip()

    if raw.startswith("美野島"):
        return "美野島"
    if raw.startswith("西中洲"):
        return "西中洲"
    if raw.startswith("冷泉"):
        return "冷泉"

    for p in PROPERTY_ORDER:
        if raw.startswith(p):
            return p

    return raw


def split_property_and_room(property_raw: str, unit_raw: str):
    raw_property = str(property_raw or "").strip()
    raw_unit = str(unit_raw or "").strip()

    normalized_property = normalize_property_name(raw_property)
    room_name = raw_unit.strip()

    if not room_name and raw_property.startswith(normalized_property):
        rest = raw_property[len(normalized_property):].strip()
        room_name = rest

    return normalized_property, room_name


def calc_gap_nights(checkout_date: str | None, next_checkin_date: str | None):
    if not checkout_date or not next_checkin_date:
        return 0
    try:
        d1 = datetime.fromisoformat(checkout_date).date()
        d2 = datetime.fromisoformat(next_checkin_date).date()
        return max((d2 - d1).days, 0)
    except Exception:
        return 0


def calc_stay_nights(checkin_date: str | None, checkout_date: str | None):
    if not checkin_date or not checkout_date:
        return 0
    try:
        d1 = datetime.fromisoformat(checkin_date).date()
        d2 = datetime.fromisoformat(checkout_date).date()
        return max((d2 - d1).days, 0)
    except Exception:
        return 0


def calc_load_score(guest_count: int, gap_nights: int) -> int:
    score = guest_count or 0
    if gap_nights == 0:
        score += 2
    elif gap_nights == 1:
        score += 1
    return score


def append_sync_alert(
    existing_note: str | None,
    old_next_checkin: str | None,
    new_next_checkin: str,
    old_task_date: str,
) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    old_next = old_next_checkin or "未設定"
    alert = (
        f"【要再割当／Beds24予約変更 {timestamp}】\n"
        f"次チェックイン: {old_next} → {new_next_checkin}\n"
        f"清掃日: {old_task_date} → {new_next_checkin}\n"
        "持越を自動解除し、担当者・チェッカーを解除しました。"
    )
    note = str(existing_note or "").strip()
    return f"{alert}\n\n{note}" if note else alert


def _has_assignment(row: dict) -> bool:
    return bool(
        row.get("assigned_staff_id")
        or row.get("assigned_staff_ids")
        or row.get("assigned_staff_name")
        or row.get("assigned_staff_names")
    )


def _status_priority(status: str | None) -> int:
    value = str(status or "").strip()
    return {
        "持越": 60,
        "清掃中": 50,
        "清掃開始": 45,
        "清掃完了": 40,
        "完了": 40,
        "CXL": 10,
        "未着手": 20,
    }.get(value, 0)


def _parse_sort_timestamp(value) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _keeper_score(row: dict, booking_id: str) -> tuple:
    """
    重複時に残すレコードを決める。
    運用状態を持つレコードを最優先し、同条件なら現在の booking_id と更新日時を優先する。
    """
    return (
        _status_priority(row.get("status")),
        1 if _has_assignment(row) else 0,
        1 if row.get("checker_id") or row.get("checker_name") else 0,
        1 if row.get("assignment_locked") else 0,
        1 if str(row.get("booking_id") or "") == booking_id else 0,
        _parse_sort_timestamp(row.get("updated_at") or row.get("created_at")),
    )


def _dedupe_rows(rows: list[dict]) -> list[dict]:
    result = []
    seen = set()
    for row in rows:
        row_id = str(row.get("id") or "")
        key = row_id or (
            str(row.get("booking_id") or ""),
            str(row.get("room_key") or ""),
            str(row.get("checkout_date") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def find_existing_candidates(payload: dict) -> list[dict]:
    """
    booking_id と「部屋 + チェックアウト日」の双方で既存レコードを探す。
    予約IDが変わった場合や手動作成済みの場合でも、同じ清掃を1件へ集約できる。
    """
    candidates = []

    booking_id = str(payload.get("booking_id") or "").strip()
    if booking_id:
        booking_res = (
            supabase.table("cleaning_tasks")
            .select("*")
            .eq("booking_id", booking_id)
            .execute()
        )
        candidates.extend(booking_res.data or [])

    room_key = str(payload.get("room_key") or "").strip()
    checkout_date = str(payload.get("checkout_date") or "").strip()
    if room_key and checkout_date:
        canonical_res = (
            supabase.table("cleaning_tasks")
            .select("*")
            .eq("room_key", room_key)
            .eq("checkout_date", checkout_date)
            .execute()
        )
        candidates.extend(canonical_res.data or [])

    return _dedupe_rows(candidates)


def preserve_operational_fields(payload: dict, existing: dict | None) -> dict:
    """
    Beds24 は予約情報だけを更新する。
    ステータス、担当、チェッカー、備考、手動変更した清掃日は既存値を維持する。
    """
    if not existing:
        return payload

    for key in OPERATIONAL_FIELDS:
        if key in existing:
            payload[key] = existing.get(key)

    return payload


def apply_carry_over_safety(payload: dict, existing: dict | None):
    """
    Beds24再同期で既存の持越設定を壊さないようにする。

    - 持越先が新しい次チェックイン日以前なら、持越日・担当情報を維持する。
    - 持越先が新しい次チェックイン日より後なら、清掃日を次チェックイン日に戻し、
      ステータスを未着手にして担当者・チェッカーを解除する。
    """
    if not existing or str(existing.get("status") or "").strip() != CARRY_OVER_STATUS:
        return payload, None

    old_task_date = str(existing.get("task_date") or "").strip()
    new_next_checkin = str(payload.get("next_checkin_date") or "").strip()
    old_next_checkin = str(existing.get("next_checkin_date") or "").strip() or None

    if not old_task_date or not new_next_checkin:
        payload["task_date"] = old_task_date or payload.get("task_date")
        payload["status"] = CARRY_OVER_STATUS
        return payload, None

    try:
        carry_date = datetime.fromisoformat(old_task_date).date()
        next_checkin_date = datetime.fromisoformat(new_next_checkin).date()
    except Exception:
        payload["task_date"] = old_task_date
        payload["status"] = CARRY_OVER_STATUS
        return payload, None

    if carry_date <= next_checkin_date:
        payload["task_date"] = old_task_date
        payload["status"] = CARRY_OVER_STATUS
        return payload, None

    payload["task_date"] = new_next_checkin
    payload["status"] = CARRY_OVER_RESET_STATUS
    payload["assigned_staff_ids"] = []
    payload["assigned_staff_names"] = []
    payload["assigned_staff_id"] = None
    payload["assigned_staff_name"] = None
    payload["checker_id"] = None
    payload["checker_name"] = None
    payload["assignment_locked"] = False
    payload["cleaning_started_at"] = None
    payload["note"] = append_sync_alert(
        existing.get("note"), old_next_checkin, new_next_checkin, old_task_date
    )

    return payload, {
        "booking_id": payload.get("booking_id"),
        "property_name": payload.get("property_name"),
        "room_name": payload.get("room_name"),
        "old_task_date": old_task_date,
        "new_task_date": new_next_checkin,
        "old_next_checkin_date": old_next_checkin,
        "new_next_checkin_date": new_next_checkin,
        "reason": "carry_over_exceeded_new_next_checkin",
    }


def save_canonical_cleaning_task(payload: dict):
    """
    1清掃 = room_key + checkout_date の1レコードとして保存する。
    既存重複があれば、運用状態を最も多く持つ1件へ予約情報を統合し、残りを削除する。
    """
    candidates = find_existing_candidates(payload)
    keeper = None
    duplicates = []

    if candidates:
        booking_id = str(payload.get("booking_id") or "")
        ordered = sorted(
            candidates,
            key=lambda row: _keeper_score(row, booking_id),
            reverse=True,
        )
        keeper = ordered[0]
        duplicates = ordered[1:]

        payload = preserve_operational_fields(payload, keeper)
        payload, adjustment = apply_carry_over_safety(payload, keeper)

        update_res = (
            supabase.table("cleaning_tasks")
            .update(payload)
            .eq("id", keeper["id"])
            .execute()
        )

        removed = []
        for duplicate in duplicates:
            duplicate_id = duplicate.get("id")
            if not duplicate_id:
                continue
            supabase.table("cleaning_tasks").delete().eq("id", duplicate_id).execute()
            removed.append({
                "id": duplicate_id,
                "booking_id": duplicate.get("booking_id"),
                "status": duplicate.get("status"),
            })

        return {
            "mode": "updated",
            "saved_count": len(update_res.data or []),
            "keeper_id": keeper.get("id"),
            "adjustment": adjustment,
            "removed_duplicates": removed,
        }

    insert_res = supabase.table("cleaning_tasks").insert(payload).execute()
    inserted = (insert_res.data or [None])[0]
    return {
        "mode": "inserted",
        "saved_count": len(insert_res.data or []),
        "keeper_id": inserted.get("id") if inserted else None,
        "adjustment": None,
        "removed_duplicates": [],
    }


def beds24_csv_sync_service(from_date: str | None = None, to_date: str | None = None):
    if not BEDS24_CSV_USERNAME or not BEDS24_CSV_PASSWORD:
        raise HTTPException(
            status_code=500,
            detail="BEDS24_CSV_USERNAME or BEDS24_CSV_PASSWORD is not set",
        )

    today = date.today()

    # デフォルト: 明日から60日先まで
    start = today + timedelta(days=1)
    end = start + timedelta(days=60)

    start_date_str = (
        from_date if from_date and from_date != "string" else format_date_string(start)
    )
    end_date_str = (
        to_date if to_date and to_date != "string" else format_date_string(end)
    )

    request_payload = {
        "username": BEDS24_CSV_USERNAME,
        "password": BEDS24_CSV_PASSWORD,
        "datefrom": start_date_str,
        "dateto": end_date_str,
    }

    try:
        res = requests.post(BEDS24_CSV_URL, data=request_payload, timeout=60)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Beds24 CSV request failed: {str(e)}",
        )

    if res.status_code != 200:
        raise HTTPException(status_code=res.status_code, detail=res.text)

    csv_rows = parse_csv_text(res.text)
    if not csv_rows:
        return {
            "ok": True,
            "from": start_date_str,
            "to": end_date_str,
            "csv_row_count": 0,
            "cleaning_saved_count": 0,
            "carry_over_adjusted_count": 0,
            "duplicate_removed_count": 0,
            "skipped_count": 0,
            "cleaning_saved": [],
            "carry_over_adjusted": [],
            "duplicates_removed": [],
            "skipped": [],
        }

    target_columns = find_target_columns(csv_rows[0])

    cleaning_saved = []
    carry_over_adjusted = []
    duplicates_removed = []
    skipped = []

    # 1. 予約行を中間配列へ格納
    records = []

    for i in range(1, len(csv_rows)):
        row = csv_rows[i]

        try:
            title = safe_get(row, target_columns["Title"])
            status = safe_get(row, target_columns["Status"])
            property_name_raw = safe_get(row, target_columns["Property"])
            unit_raw = safe_get(row, target_columns["Unit"])
            first_night_raw = safe_get(row, target_columns["FirstNight"])
            check_out_raw = safe_get(row, target_columns["Check Out"])
            adult_raw = safe_get(row, target_columns["Adult"])
            child_raw = safe_get(row, target_columns["Child"])
            ref_raw = safe_get(row, target_columns["Ref"]).strip()

            if not ref_raw:
                skipped.append({"reason": "missing ref", "row_index": i})
                continue

            if status == "Cancelled":
                skipped.append({"reason": "cancelled", "row_index": i, "booking_id": ref_raw})
                continue

            if "ブロック" in title or "予備部屋" in title:
                skipped.append({"reason": "blocked", "row_index": i, "booking_id": ref_raw})
                continue

            checkin_date = parse_beds24_date(first_night_raw)
            checkout_date = parse_beds24_date(check_out_raw)

            if not checkin_date or not checkout_date:
                skipped.append({"reason": "missing date", "row_index": i, "booking_id": ref_raw})
                continue

            property_name, room_name = split_property_and_room(property_name_raw, unit_raw)
            room_key = f"{property_name}{room_name}"
            booking_id = ref_raw

            try:
                adult_count = int(adult_raw or 0)
            except Exception:
                adult_count = 0

            try:
                child_count = int(child_raw or 0)
            except Exception:
                child_count = 0

            guest_count = adult_count + child_count

            records.append({
                "booking_id": booking_id,
                "property_name": property_name,
                "room_name": room_name,
                "room_key": room_key,
                "checkin_date": checkin_date,
                "checkout_date": checkout_date,
                "guest_count": guest_count,
            })

        except Exception as e:
            skipped.append({
                "reason": f"row process error: {str(e)}",
                "row_index": i,
            })

    # 2. 部屋ごとにグループ化
    grouped = defaultdict(list)
    for rec in records:
        grouped[rec["room_key"]].append(rec)

    # 3. 各部屋で「次の予約」を参照して cleaning_tasks 用の値を作成
    final_records = []

    for room_key, room_records in grouped.items():
        room_records.sort(key=lambda x: (x["checkin_date"], x["checkout_date"]))

        for idx, rec in enumerate(room_records):
            next_checkin_date = None
            next_guest_count = 0
            next_stay_nights = 0

            if idx + 1 < len(room_records):
                next_rec = room_records[idx + 1]
                next_checkin_date = next_rec["checkin_date"]
                next_guest_count = next_rec["guest_count"] or 0
                next_stay_nights = calc_stay_nights(
                    next_rec["checkin_date"],
                    next_rec["checkout_date"],
                )

            gap_nights = calc_gap_nights(rec["checkout_date"], next_checkin_date)
            load_score = calc_load_score(rec["guest_count"], gap_nights)

            final_records.append({
                "booking_id": rec["booking_id"],
                "property_name": rec["property_name"],
                "room_name": rec["room_name"],
                "room_key": rec["room_key"],
                "task_date": rec["checkout_date"],
                "checkout_date": rec["checkout_date"],
                "next_checkin_date": next_checkin_date,
                "gap_nights": gap_nights,
                "guest_count": rec["guest_count"],
                "next_guest_count": next_guest_count,
                "next_stay_nights": next_stay_nights,
                "load_score": load_score,
                "status": "未着手",
                "note": "",
                "source": "beds24_csv",
            })

    # 4. room_key + checkout_date を正規キーとして保存し、既存重複を統合する。
    for payload in final_records:
        try:
            result = save_canonical_cleaning_task(payload)

            cleaning_saved.append({
                "booking_id": payload["booking_id"],
                "mode": result["mode"],
                "keeper_id": result["keeper_id"],
                "count": result["saved_count"],
            })

            if result["adjustment"]:
                carry_over_adjusted.append(result["adjustment"])

            if result["removed_duplicates"]:
                duplicates_removed.append({
                    "room_key": payload["room_key"],
                    "checkout_date": payload["checkout_date"],
                    "keeper_id": result["keeper_id"],
                    "removed": result["removed_duplicates"],
                })

        except Exception as e:
            skipped.append({
                "reason": f"save error: {str(e)}",
                "booking_id": payload["booking_id"],
                "room_key": payload.get("room_key"),
                "checkout_date": payload.get("checkout_date"),
            })

    duplicate_removed_count = sum(
        len(item.get("removed") or []) for item in duplicates_removed
    )

    return {
        "ok": True,
        "from": start_date_str,
        "to": end_date_str,
        "csv_row_count": len(csv_rows) - 1,
        "cleaning_saved_count": len(cleaning_saved),
        "carry_over_adjusted_count": len(carry_over_adjusted),
        "duplicate_removed_count": duplicate_removed_count,
        "skipped_count": len(skipped),
        "cleaning_saved": cleaning_saved[:20],
        "carry_over_adjusted": carry_over_adjusted[:50],
        "duplicates_removed": duplicates_removed[:50],
        "skipped": skipped[:50],
    }
