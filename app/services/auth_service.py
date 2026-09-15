import os
import jwt
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.db import supabase

security = HTTPBearer()
JWT_SECRET = os.getenv("JWT_SECRET", "change-me-secret")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 12


def create_access_token(user_id: str, role: str) -> str:
    payload = {"user_id": user_id, "role": role, "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS)}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = payload.get("user_id")
        role = payload.get("role")
        if not user_id: raise HTTPException(status_code=401, detail="認証情報が不正です。")
        return {"user_id": user_id, "role": role}
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="ログイン有効期限が切れています。")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="トークンが不正です。")


def get_current_user_id(current_user: dict = Depends(get_current_user)) -> str:
    return current_user["user_id"]


def require_master_admin(current_user: dict = Depends(get_current_user)) -> dict:
    """最高管理者APIではJWTのroleだけを信用せず、現在のDB権限と有効状態を毎回確認する。"""
    user_id = current_user.get("user_id")
    try:
        res = supabase.table("staff_members").select("id,staff_name,role,is_active").eq("id", user_id).limit(1).execute()
    except Exception:
        raise HTTPException(status_code=503, detail="最高管理者権限を確認できませんでした。")
    if not res.data:
        raise HTTPException(status_code=403, detail="最高管理者権限がありません。")
    staff = res.data[0]
    if staff.get("is_active") is not True:
        raise HTTPException(status_code=403, detail="このアカウントは無効です。")
    if staff.get("role") != "master_admin":
        raise HTTPException(status_code=403, detail="最高管理者権限がありません。")
    return {"user_id": user_id, "role": "master_admin", "staff_name": staff.get("staff_name")}


def require_admin_or_leader(current_user: dict = Depends(get_current_user)) -> dict:
    role = current_user.get("role")
    if role not in ["master_admin", "admin", "leader", "sub_admin", "operation", "payroll_admin"]: raise HTTPException(status_code=403, detail="管理画面にアクセスできません。")
    return current_user


def require_prep_access(current_user: dict = Depends(get_current_user)) -> dict:
    role = current_user.get("role")
    if role not in ["master_admin", "admin", "sub_admin", "leader", "operation", "payroll_admin", "prep_viewer"]: raise HTTPException(status_code=403, detail="準備物確認画面にアクセスできません。")
    return current_user


def require_operational_write(current_user: dict = Depends(get_current_user)) -> dict:
    role = current_user.get("role")
    if role not in ["master_admin", "admin", "sub_admin", "leader", "operation", "payroll_admin"]: raise HTTPException(status_code=403, detail="この操作を実行する権限がありません。")
    return current_user


def require_task_write(current_user: dict = Depends(get_current_user)) -> dict:
    role = current_user.get("role")
    if role not in ["master_admin", "admin", "sub_admin", "leader", "operation", "payroll_admin", "staff", "checker", "contractor"]: raise HTTPException(status_code=403, detail="タスクを更新する権限がありません。")
    return current_user


def require_admin_write(current_user: dict = Depends(get_current_user)) -> dict:
    role = current_user.get("role")
    if role not in ["master_admin", "admin", "sub_admin"]: raise HTTPException(status_code=403, detail="この操作は管理者のみ実行できます。")
    return current_user


def require_shift_write(current_user: dict = Depends(get_current_user)) -> dict:
    role = current_user.get("role")
    if role not in ["master_admin", "admin", "sub_admin"]: raise HTTPException(status_code=403, detail="シフト表は閲覧のみ可能です。")
    return current_user


def require_worklog_write(current_user: dict = Depends(get_current_user)) -> dict:
    role = current_user.get("role")
    if role not in ["master_admin", "admin", "sub_admin", "payroll_admin"]: raise HTTPException(status_code=403, detail="このアカウントは閲覧専用です。")
    return current_user


def require_payroll_access(request: Request, current_user: dict = Depends(get_current_user)) -> dict:
    role = current_user.get("role")
    read_roles = ["master_admin", "admin", "sub_admin", "leader", "payroll_admin"]
    write_roles = ["master_admin", "admin", "sub_admin", "payroll_admin"]
    if role not in (read_roles if request.method == "GET" else write_roles): raise HTTPException(status_code=403, detail="給与・勤怠機能を利用する権限がありません。")
    return current_user
