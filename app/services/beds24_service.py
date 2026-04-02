import csv
import io
import os
import requests
from datetime import date, datetime, timedelta

from fastapi import HTTPException

from app.db import supabase


BEDS24_CSV_URL = os.getenv("BEDS24_CSV_URL", "https://www.beds24.com/api/csv/getbookingscsv")
BEDS24_CSV_USERNAME = os.getenv("BEDS24_CSV_USERNAME", "")
BEDS24_CSV_PASSWORD = os.getenv("BEDS24_CSV_PASSWORD", "")


def format_jst_date_string(dt: date) -> str:
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
    }

    for i, h in enumerate(header_row):
        header = str(h).strip()
        if header in target_columns:
            target_columns[header] = i

    required = [
        "Title",
        "Property",
        "Unit",
        "FirstNight",
        "Check Out",
        "Status",
    ]

    missing = [k for k in required if target_columns[k] == -1]
    if missing:
        raise HTTPException(status_code=500, detail=f"required csv columns missing: {', '.join(missing)}")

    return target_columns


def split_property_and_room(property_raw, unit_raw):
    property_raw = str(property_raw or "").strip()
    unit_raw = str(unit_raw or "").strip()

    property_list = [
        "FFFホテル", "やなぎ橋", "住吉", "アクシオン美野島", "ブランシェ", "ウィングス", "美野島",
        "玉井", "ウーブル博多", "いそのビル", "ジェン", "ルッシェ", "東光", "グランデエス",
        "エスコート", "アトラス", "薬院", "ロイズ", "ピット", "県庁前",
        "西中洲", "冷泉", "駅前モダン", "比恵モダン", "浄水"
    ]

    for p in property_list:
        if property_raw.startswith(p):
            rest = property_raw.replace(p, "").strip()
            if not unit_raw and rest:
                unit_raw = rest
            return p, unit_raw

    return property_raw, unit_raw


def calc_load_score(guest_count: int, gap_nights: int) -> int:
    score = guest_count or 0
    if gap_nights == 0:
        score += 2
    elif gap_nights == 1:
        score += 1
    return score


def beds24_csv_sync_service(from_date: str | None = None, to_date: str | None = None):
    if not BEDS24_CSV_USERNAME or not BEDS24_CSV_PASSWORD:
        raise HTTPException(status_code=500, detail="BEDS24_CSV_USERNAME or BEDS24_CSV_PASSWORD is not set")

    today = date.today()

    start_date_str = from_date if from_date and from_date != "string" else format_jst_date_string(today)

    if to_date and to_date != "string":
        end_date_str = to_date
    else:
        next_month_end = date(today.year, today.month, 1) + timedelta(days=62)
        next_month_end = date(next_month_end.year, next_month_end.month, 1) - timedelta(days=1)
        end_date_str = format_jst_date_string(next_month_end)

    payload = {
        "username": BEDS24_CSV_USERNAME,
        "password": BEDS24_CSV_PASSWORD,
        "datefrom": start_date_str,
        "dateto": end_date_str,
    }

    try:
        res = requests.post(BEDS24_CSV_URL, data=payload, timeout=60)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Beds24 CSV request failed: {str(e)}")

    if res.status_code != 200:
        raise HTTPException(status_code=res.status_code, detail=res.text)

    csv_rows = parse_csv_text(res.text)
    if not csv_rows:
        return {"ok": True, "csv_row_count": 0, "cleaning_saved_count": 0}

    target_columns = find_target_columns(csv_rows[0])
    cleaning_saved = []
    skipped = []

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

            if status == "Cancelled":
                skipped.append({"reason": "cancelled", "row_index": i})
                continue

            if "ブロック" in title or "予備部屋" in title:
                skipped.append({"reason": "blocked", "row_index": i})
                continue

            checkin_date = parse_beds24_date(first_night_raw)
            checkout_date = parse_beds24_date(check_out_raw)

            if not checkout_date:
                skipped.append({"reason": "missing checkout", "row_index": i})
                continue

            property_name_normalized, unit_normalized = split_property_and_room(property_name_raw, unit_raw)
            room_key = f"{property_name_normalized}{unit_normalized}"
            booking_id = f"{property_name_raw}_{unit_raw}_{first_night_raw}_{check_out_raw}_{i}"

            try:
                adult_count = int(adult_raw or 0)
            except Exception:
                adult_count = 0

            try:
                child_count = int(child_raw or 0)
            except Exception:
                child_count = 0

            guest_count = adult_count + child_count
            gap_nights = 0

            cleaning_payload = {
                "booking_id": booking_id,
                "property_name": property_name_normalized,
                "room_name": unit_normalized,
                "room_key": room_key,
                "task_date": checkout_date,
                "checkout_date": checkout_date,
                "next_checkin_date": checkin_date,
                "gap_nights": gap_nights,
                "guest_count": guest_count,
                "load_score": calc_load_score(guest_count, gap_nights),
                "status": "未着手",
                "note": "",
                "source": "beds24_csv",
            }

            cleaning_res = (
                supabase.table("cleaning_tasks")
                .upsert(cleaning_payload, on_conflict="booking_id")
                .execute()
            )

            cleaning_saved.append({
                "booking_id": booking_id,
                "cleaning_count": len(cleaning_res.data or []),
            })

        except Exception as e:
            skipped.append({"reason": f"row process error: {str(e)}", "row_index": i})

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
