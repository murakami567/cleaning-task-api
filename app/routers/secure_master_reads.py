from fastapi import APIRouter, Depends, HTTPException

from app.db import supabase
from app.logger import get_logger
from app.services.auth_service import require_admin_or_leader

router = APIRouter(tags=["secure-master-reads"])
logger = get_logger(__name__)

PROPERTY_LIST_FIELDS = (
    "id,property_code,property_name,normalized_name,sort_order,is_active,"
    "max_assignable_count,assignment_mode,cleaning_point,task_color,address,"
    "google_maps_url,entrance_number"
)

# 管理画面では従来どおり鍵番号・Wi-Fi情報を一覧に含める。
# このAPI自体は admin / sub_admin / leader の認証必須。
ROOM_LIST_FIELDS = (
    "id,property_id,room_name,room_code,room_key,normalized_room_key,capacity,"
    "room_sort_order,is_active,prep_d,prep_s,prep_spare_s,prep_ta,cleaning_score,"
    "keybox_number,spare_key_number,mailbox_number,wifi_ssid,wifi_password,note"
)

ROOM_CREDENTIAL_FIELDS = (
    "id,keybox_number,spare_key_number,mailbox_number,wifi_ssid,wifi_password"
)


def _fetch_properties():
    try:
        res = (
            supabase.table("properties")
            .select(PROPERTY_LIST_FIELDS)
            .order("sort_order")
            .order("property_name")
            .execute()
        )
        return res.data or []
    except Exception as e:
        logger.error(f"secure property fetch failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="物件一覧の取得に失敗しました。")


def _fetch_rooms(property_id: str | None = None):
    try:
        query = supabase.table("rooms").select(ROOM_LIST_FIELDS)
        if property_id:
            query = query.eq("property_id", property_id)
        res = query.order("room_sort_order").order("room_name").execute()
        return res.data or []
    except Exception as e:
        logger.error(f"secure room fetch failed: property_id={property_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="部屋一覧の取得に失敗しました。")


@router.get("/admin/master/properties")
def get_secure_properties(current_user: dict = Depends(require_admin_or_leader)):
    return _fetch_properties()


@router.get("/admin/master/rooms")
def get_secure_rooms(
    property_id: str | None = None,
    current_user: dict = Depends(require_admin_or_leader),
):
    return _fetch_rooms(property_id)


@router.get("/admin/master/rooms/{room_id}/credentials")
def get_room_credentials(
    room_id: str,
    current_user: dict = Depends(require_admin_or_leader),
):
    try:
        res = (
            supabase.table("rooms")
            .select(ROOM_CREDENTIAL_FIELDS)
            .eq("id", room_id)
            .limit(1)
            .execute()
        )
    except Exception as e:
        logger.error(f"room credentials fetch failed: room_id={room_id} {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="部屋の機密情報取得に失敗しました。")

    if not res.data:
        raise HTTPException(status_code=404, detail="部屋が見つかりません。")
    return res.data[0]
