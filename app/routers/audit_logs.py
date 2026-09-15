from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from app.db import supabase
from app.logger import get_logger
from app.services.audit_service import sanitize_audit_data
from app.services.auth_service import get_current_user, require_master_admin
router=APIRouter(tags=["audit_logs"]);logger=get_logger(__name__)
class AuditLogCreate(BaseModel):
 source:Literal["admin","mobile","employee","master","system"];action:str=Field(min_length=1,max_length=100);page:str|None=Field(default=None,max_length=200);target_type:str|None=Field(default=None,max_length=100);target_id:str|None=Field(default=None,max_length=200);target_name:str|None=Field(default=None,max_length=300);before_data:dict[str,Any]|None=None;after_data:dict[str,Any]|None=None;result:Literal["success","failure"]="success";error_message:str|None=Field(default=None,max_length=1000);metadata:dict[str,Any]=Field(default_factory=dict)
def _actor_snapshot(user_id:str)->tuple[str|None,str|None]:
 try:
  res=supabase.table("staff_members").select("staff_name,role").eq("id",user_id).limit(1).execute()
  if res.data:return res.data[0].get("staff_name"),res.data[0].get("role")
 except Exception as exc:logger.warning(f"audit actor lookup failed user_id={user_id}: {exc}")
 return None,None
def _parse_range(start_at:str|None,end_at:str|None,period_minutes:int)->tuple[datetime,datetime]:
 now=datetime.now(timezone.utc)
 try:end=datetime.fromisoformat(end_at.replace("Z","+00:00")).astimezone(timezone.utc) if end_at else now
 except (ValueError,AttributeError):raise HTTPException(status_code=400,detail="終了日時の形式が正しくありません。")
 try:start=datetime.fromisoformat(start_at.replace("Z","+00:00")).astimezone(timezone.utc) if start_at else end-timedelta(minutes=period_minutes)
 except (ValueError,AttributeError):raise HTTPException(status_code=400,detail="開始日時の形式が正しくありません。")
 if start>=end:raise HTTPException(status_code=400,detail="開始日時は終了日時より前にしてください。")
 if end-start>timedelta(days=30):raise HTTPException(status_code=400,detail="表示期間は最大30日です。")
 return start,end
def _trend(rows:list[dict],start:datetime,end:datetime,bucket_minutes:int)->list[dict]:
 span=(end-start).total_seconds()/60;count=int(span//bucket_minutes)+(1 if span%bucket_minutes else 0)
 if count>2000:raise HTTPException(status_code=400,detail="集計点が多すぎます。期間を短くするか集計間隔を大きくしてください。")
 buckets=[]
 for i in range(count):
  t=start+timedelta(minutes=i*bucket_minutes);buckets.append({"hour":t.isoformat(),"operations":0,"failures":0})
 for row in rows:
  try:
   created=datetime.fromisoformat(str(row.get("created_at","")).replace("Z","+00:00")).astimezone(timezone.utc);idx=int((created-start).total_seconds()//(bucket_minutes*60))
   if 0<=idx<len(buckets):buckets[idx]["operations"]+=1;buckets[idx]["failures"]+=1 if row.get("result")=="failure" else 0
  except (TypeError,ValueError):continue
 return buckets
def _read_rows(start:datetime,end:datetime,columns:str)->list[dict]:
 return supabase.table("audit_logs").select(columns).gte("created_at",start.isoformat()).lt("created_at",end.isoformat()).order("created_at",desc=True).limit(20000).execute().data or []
@router.post("/api/audit-logs",status_code=201)
def create_audit_log(payload:AuditLogCreate,current_user:dict=Depends(get_current_user)):
 user_id=current_user["user_id"];actor_name,db_role=_actor_snapshot(user_id);row={"actor_id":user_id,"actor_name":actor_name,"actor_role":db_role or current_user.get("role"),"source":payload.source,"action":payload.action,"page":payload.page,"target_type":payload.target_type,"target_id":payload.target_id,"target_name":payload.target_name,"before_data":sanitize_audit_data(payload.before_data),"after_data":sanitize_audit_data(payload.after_data),"result":payload.result,"error_message":payload.error_message,"metadata":sanitize_audit_data(payload.metadata)}
 try:res=supabase.table("audit_logs").insert(row).execute()
 except Exception as exc:logger.error(f"audit log insert failed user_id={user_id}: {exc}",exc_info=True);raise HTTPException(status_code=500,detail="監査ログの保存に失敗しました。")
 if not res.data:raise HTTPException(status_code=500,detail="監査ログの保存に失敗しました。")
 return {"ok":True,"id":res.data[0].get("id")}
@router.get("/api/master/audit-logs")
def list_audit_logs(limit:int=Query(default=100,ge=1,le=500),offset:int=Query(default=0,ge=0),source:str|None=Query(default=None),actor_id:str|None=Query(default=None),action:str|None=Query(default=None),current_user:dict=Depends(require_master_admin)):
 try:
  query=supabase.table("audit_logs").select("*").order("created_at",desc=True)
  if source:query=query.eq("source",source)
  if actor_id:query=query.eq("actor_id",actor_id)
  if action:query=query.eq("action",action)
  res=query.range(offset,offset+limit-1).execute()
 except Exception as exc:logger.error(f"audit log read failed master_user_id={current_user.get('user_id')}: {exc}",exc_info=True);raise HTTPException(status_code=500,detail="監査ログの取得に失敗しました。")
 return {"items":res.data or [],"limit":limit,"offset":offset}
@router.get("/api/master/account-audit")
def account_permission_audit(current_user:dict=Depends(require_master_admin)):
 try:staff=supabase.table("staff_members").select("id,staff_code,staff_name,role,is_active,created_at,updated_at").order("staff_code").execute().data or [];history=supabase.table("audit_logs").select("id,actor_name,actor_role,action,target_id,target_name,before_data,after_data,result,created_at").in_("action",["staff_create","staff_update"]).order("created_at",desc=True).limit(100).execute().data or []
 except Exception as exc:logger.error(f"account audit read failed master_user_id={current_user.get('user_id')}: {exc}",exc_info=True);raise HTTPException(status_code=500,detail="アカウント・権限監査情報の取得に失敗しました。")
 privileged={"master_admin","admin","sub_admin","payroll_admin"};return {"summary":{"total":len(staff),"active":sum(x.get("is_active") is True for x in staff),"inactive":sum(x.get("is_active") is not True for x in staff),"privileged":sum(x.get("role") in privileged for x in staff),"master_admin":sum(x.get("role")=="master_admin" for x in staff)},"accounts":staff,"history":history}
@router.get("/api/master/system-monitor")
def system_monitor(period_minutes:int=Query(default=1440,ge=5,le=43200),bucket_minutes:int=Query(default=60,ge=5,le=1440),start_at:str|None=Query(default=None),end_at:str|None=Query(default=None),current_user:dict=Depends(require_master_admin)):
 start,end=_parse_range(start_at,end_at,period_minutes)
 try:rows=_read_rows(start,end,"id,actor_name,actor_role,source,action,page,target_type,target_name,result,error_message,metadata,created_at")
 except Exception as exc:logger.error(f"system monitor read failed master_user_id={current_user.get('user_id')}: {exc}",exc_info=True);raise HTTPException(status_code=500,detail="システム監視情報の取得に失敗しました。")
 failures=[x for x in rows if x.get("result")=="failure"];auth=[x for x in failures if x.get("action") in {"login","auth_failure","login_failure"} or x.get("page")=="login"];counts={k:0 for k in ["admin","mobile","employee","master","system"]}
 for x in failures:counts[x.get("source") or "system"]=counts.get(x.get("source") or "system",0)+1
 return {"status":"normal" if not failures else("warning" if len(failures)<10 else "critical"),"period_minutes":int((end-start).total_seconds()/60),"bucket_minutes":bucket_minutes,"start_at":start.isoformat(),"end_at":end.isoformat(),"summary":{"operations":len(rows),"failures":len(failures),"auth_failures":len(auth),"latest_failure":failures[0].get("created_at") if failures else None},"source_counts":counts,"hourly_trend":_trend(rows,start,end,bucket_minutes),"failures":failures[:500]}
@router.get("/api/master/dashboard")
def master_dashboard(period_minutes:int=Query(default=1440,ge=5,le=43200),bucket_minutes:int=Query(default=60,ge=5,le=1440),start_at:str|None=Query(default=None),end_at:str|None=Query(default=None),current_user:dict=Depends(require_master_admin)):
 start,end=_parse_range(start_at,end_at,period_minutes)
 try:rows=_read_rows(start,end,"source,action,page,result,created_at")
 except Exception as exc:logger.error(f"master dashboard read failed master_user_id={current_user.get('user_id')}: {exc}",exc_info=True);raise HTTPException(status_code=500,detail="最高管理者ダッシュボード情報の取得に失敗しました。")
 failures=[x for x in rows if x.get("result")=="failure"];return {"period_minutes":int((end-start).total_seconds()/60),"bucket_minutes":bucket_minutes,"start_at":start.isoformat(),"end_at":end.isoformat(),"summary":{"operations":len(rows),"failures":len(failures)},"hourly_trend":_trend(rows,start,end,bucket_minutes)}
