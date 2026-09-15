from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import supabase
from app.logger import get_logger
from app.services.audit_service import write_audit_log
from app.services.auth_service import require_admin_or_leader, require_master_admin

router = APIRouter(tags=["secure-master-reads"])
logger = get_logger(__name__)

PROPERTY_LIST_FIELDS = "id,property_code,property_name,normalized_name,sort_order,is_active,max_assignable_count,assignment_mode,cleaning_point,task_color,address,google_maps_url,entrance_number"
ROOM_LIST_FIELDS = "id,property_id,room_name,room_code,room_key,normalized_room_key,capacity,room_sort_order,is_active,prep_d,prep_s,prep_spare_s,prep_ta,cleaning_score,keybox_number,spare_key_number,mailbox_number,wifi_ssid,wifi_password,note"
ROOM_CREDENTIAL_FIELDS = "id,keybox_number,spare_key_number,mailbox_number,wifi_ssid,wifi_password"
MASTER_ACCOUNT_FIELDS = "id,staff_code,staff_name,role,is_active,sort_order,created_at,updated_at"

class MasterAccountUpdate(BaseModel):
    role: str | None = None
    is_active: bool | None = None

def _fetch_properties():
    try:
        res = supabase.table("properties").select(PROPERTY_LIST_FIELDS).order("sort_order").order("property_name").execute(); return res.data or []
    except Exception as e:
        logger.error(f"secure property fetch failed: {e}", exc_info=True); raise HTTPException(status_code=500, detail="物件一覧の取得に失敗しました。")

def _fetch_rooms(property_id: str | None = None):
    try:
        query = supabase.table("rooms").select(ROOM_LIST_FIELDS)
        if property_id: query = query.eq("property_id", property_id)
        res = query.order("room_sort_order").order("room_name").execute(); return res.data or []
    except Exception as e:
        logger.error(f"secure room fetch failed: property_id={property_id} {e}", exc_info=True); raise HTTPException(status_code=500, detail="部屋一覧の取得に失敗しました。")

@router.get("/admin/master/properties")
def get_secure_properties(current_user: dict = Depends(require_admin_or_leader)): return _fetch_properties()

@router.get("/admin/master/rooms")
def get_secure_rooms(property_id: str | None = None, current_user: dict = Depends(require_admin_or_leader)): return _fetch_rooms(property_id)

@router.get("/admin/master/rooms/{room_id}/credentials")
def get_room_credentials(room_id: str, current_user: dict = Depends(require_admin_or_leader)):
    try: res = supabase.table("rooms").select(ROOM_CREDENTIAL_FIELDS).eq("id", room_id).limit(1).execute()
    except Exception as e:
        logger.error(f"room credentials fetch failed: room_id={room_id} {e}", exc_info=True); raise HTTPException(status_code=500, detail="部屋の機密情報取得に失敗しました。")
    if not res.data: raise HTTPException(status_code=404, detail="部屋が見つかりません。")
    return res.data[0]

@router.get("/api/master/accounts")
def get_master_accounts(current_user: dict = Depends(require_master_admin)):
    try:
        res = supabase.table("staff_members").select(MASTER_ACCOUNT_FIELDS).order("sort_order").order("staff_code").execute(); accounts = res.data or []; counts={}; active_count=0
        for account in accounts:
            role=str(account.get("role") or "unknown"); counts[role]=counts.get(role,0)+1
            if account.get("is_active") is True: active_count+=1
        return {"items":accounts,"summary":{"total":len(accounts),"active":active_count,"inactive":len(accounts)-active_count,"roles":counts}}
    except Exception as e:
        logger.error(f"master account audit fetch failed: {e}", exc_info=True); raise HTTPException(status_code=500, detail="アカウント・権限情報の取得に失敗しました。")

@router.get("/api/master/accounts/{account_id}/history")
def get_master_account_history(account_id: str, current_user: dict = Depends(require_master_admin)):
    try:
        account_res=supabase.table("staff_members").select(MASTER_ACCOUNT_FIELDS).eq("id",account_id).limit(1).execute()
        if not account_res.data: raise HTTPException(status_code=404,detail="アカウントが見つかりません。")
        history_res=supabase.table("audit_logs").select("id,actor_name,actor_role,action,before_data,after_data,result,created_at").eq("target_id",account_id).in_("action",["staff_create","staff_update","master_account_update"]).order("created_at",desc=True).limit(200).execute(); history=[]
        for row in history_res.data or []:
            before=row.get("before_data") or {}; after=row.get("after_data") or {}; changes=[]
            for key in ("role","is_active"):
                if before.get(key)!=after.get(key): changes.append({"field":key,"before":before.get(key),"after":after.get(key)})
            if changes or row.get("action")=="staff_create": history.append({**row,"changes":changes})
        return {"account":account_res.data[0],"history":history}
    except HTTPException: raise
    except Exception as e:
        logger.error(f"master account history fetch failed account_id={account_id}: {e}",exc_info=True); raise HTTPException(status_code=500,detail="権限変更履歴の取得に失敗しました。")

@router.get("/api/master/settings/accounts")
def get_master_admin_accounts(current_user: dict = Depends(require_master_admin)):
    try:
        res=supabase.table("staff_members").select(MASTER_ACCOUNT_FIELDS).eq("role","master_admin").order("staff_code").execute()
        return {"items":res.data or [],"current_user_id":current_user.get("user_id")}
    except Exception as e:
        logger.error(f"master settings fetch failed: {e}",exc_info=True); raise HTTPException(status_code=500,detail="最高管理者情報の取得に失敗しました。")

@router.patch("/api/master/settings/accounts/{account_id}")
def update_master_admin_account(account_id: str, payload: MasterAccountUpdate, current_user: dict = Depends(require_master_admin)):
    actor_id=str(current_user.get("user_id") or "")
    try:
        target_res=supabase.table("staff_members").select(MASTER_ACCOUNT_FIELDS).eq("id",account_id).limit(1).execute()
        if not target_res.data: raise HTTPException(status_code=404,detail="アカウントが見つかりません。")
        before=target_res.data[0]; changes={}
        if payload.role is not None:
            if payload.role not in {"master_admin","admin"}: raise HTTPException(status_code=400,detail="最高管理者設定では管理者または最高管理者のみ指定できます。")
            changes["role"]=payload.role
        if payload.is_active is not None: changes["is_active"]=payload.is_active
        if not changes: return {"ok":True,"item":before}
        removing_master=before.get("role")=="master_admin" and changes.get("role",before.get("role"))!="master_admin"
        disabling_master=before.get("role")=="master_admin" and changes.get("is_active",before.get("is_active")) is False
        if account_id==actor_id and (removing_master or disabling_master): raise HTTPException(status_code=400,detail="自分自身の最高管理者権限を解除・無効化することはできません。")
        if removing_master or disabling_master:
            masters=supabase.table("staff_members").select("id").eq("role","master_admin").eq("is_active",True).execute()
            active_ids={str(x.get("id")) for x in masters.data or []}
            if account_id in active_ids and len(active_ids)<=1: raise HTTPException(status_code=400,detail="最後の有効な最高管理者は解除・無効化できません。")
        updated=supabase.table("staff_members").update(changes).eq("id",account_id).execute()
        if not updated.data: raise HTTPException(status_code=500,detail="最高管理者設定の更新に失敗しました。")
        after=updated.data[0]
        write_audit_log(actor_id=actor_id,actor_role=current_user.get("role"),source="master",action="master_account_update",page="master/settings",target_type="staff",target_id=account_id,target_name=before.get("staff_name"),before_data=before,after_data=after)
        return {"ok":True,"item":after}
    except HTTPException: raise
    except Exception as e:
        logger.error(f"master settings update failed account_id={account_id}: {e}",exc_info=True); raise HTTPException(status_code=500,detail="最高管理者設定の更新に失敗しました。")
