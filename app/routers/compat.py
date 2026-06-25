from datetime import date
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from app.db import supabase
from app.logger import get_logger

router = APIRouter(tags=["compat"])
logger = get_logger(__name__)


def _safe_data(res: Any):
    return res.data if getattr(res, "data", None) is not None else []


@router.get("/properties")
def get_properties():
    try:
        res = (
            supabase.table("properties")
            .select("*")
            .order("sort_order")
            .order("property_name")
            .execute()
        )
        return _safe_data(res)
    except Exception as e:
        logger.error(f"compat get_properties failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="properties fetch failed")


@router.get("/rooms")
def get_rooms(property_id: str | None = None):
    try:
        q = supabase.table("rooms").select("*")
        if property_id:
            q = q.eq("property_id", property_id)
        res = q.order("room_sort_order").order("room_name").execute()
        return _safe_data(res)
    except Exception as e:
        logger.error(f"compat get_rooms failed: property_id={property_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="rooms fetch failed")


@router.get("/shifts")
def get_shifts(shift_date: str | None = None):
    try:
        q = supabase.table("shift_days").select("*, shift_entries(*, staff_members(*))")
        if shift_date:
            q = q.eq("shift_date", shift_date)
        else:
            q = q.eq("shift_date", date.today().isoformat())
        res = q.order("shift_date").execute()
        return _safe_data(res)
    except Exception as e:
        logger.error(f"compat get_shifts failed: shift_date={shift_date} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="shifts fetch failed")


@router.post("/shifts/create_day")
def create_shift_day(shift_date: str = Body(...), note: str = Body("")):
    try:
        existing = (
            supabase.table("shift_days")
            .select("*")
            .eq("shift_date", shift_date)
            .limit(1)
            .execute()
        )
        if existing.data:
            return existing.data[0]

        res = (
            supabase.table("shift_days")
            .insert({"shift_date": shift_date, "note": note})
            .execute()
        )
        if not res.data:
            raise HTTPException(status_code=500, detail="shift day creation failed")
        return res.data[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"compat create_shift_day failed: shift_date={shift_date} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="shift day creation failed")


@router.get("/staff-schedules")
def get_staff_schedules(shift_date: str):
    try:
        res = (
            supabase.table("shift_days")
            .select("*, shift_entries(*, staff_members(*))")
            .eq("shift_date", shift_date)
            .execute()
        )
        day = res.data[0] if res.data else None
        if not day:
            return {"shift_date": shift_date, "entries": [], "shift_entries": []}
        return {
            "shift_date": shift_date,
            "id": day.get("id"),
            "entries": day.get("shift_entries") or [],
            "shift_entries": day.get("shift_entries") or [],
        }
    except Exception as e:
        logger.error(f"compat get_staff_schedules failed: shift_date={shift_date} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="staff schedules fetch failed")


@router.get("/shift-board")
def get_shift_board(year: int, month: int):
    try:
        start = date(year, month, 1)
        if month == 12:
            end = date(year + 1, 1, 1)
        else:
            end = date(year, month + 1, 1)

        res = (
            supabase.table("shift_days")
            .select("*, shift_entries(*, staff_members(*))")
            .gte("shift_date", start.isoformat())
            .lt("shift_date", end.isoformat())
            .order("shift_date")
            .execute()
        )
        return {"year": year, "month": month, "days": _safe_data(res)}
    except Exception as e:
        logger.error(f"compat get_shift_board failed: year={year} month={month} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="shift board fetch failed")
