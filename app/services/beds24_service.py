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

    payload = {
        "username": BEDS24_CSV_USERNAME,
        "password": BEDS24_CSV_PASSWORD,
        "datefrom": start_date_str,
        "dateto": end_date_str,
    }

    try:
        res = requests.post(BEDS24_CSV_URL, data=payload, timeout=60)
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
            "skipped_count": 0,
            "cleaning_saved": [],
            "skipped": [],
        }

    target_columns = find_target_columns(csv_rows[0])

    cleaning_saved = []
    skipped = []

    # 1. まず予約行を中間配列へ格納
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

    # 4. DBへupsert
    for payload in final_records:
        try:
            upsert_res = (
                supabase.table("cleaning_tasks")
                .upsert(payload, on_conflict="booking_id")
                .execute()
            )

            cleaning_saved.append({
                "booking_id": payload["booking_id"],
                "count": len(upsert_res.data or []),
            })

        except Exception as e:
            skipped.append({
                "reason": f"upsert error: {str(e)}",
                "booking_id": payload["booking_id"],
            })

    return {
        "ok": True,
        "from": start_date_str,
        "to": end_date_str,
        "csv_row_count": len(csv_rows) - 1,
        "cleaning_saved_count": len(cleaning_saved),
        "skipped_count": len(skipped),
        "cleaning_saved": cleaning_saved[:20],
        "skipped": skipped[:50],
    }
