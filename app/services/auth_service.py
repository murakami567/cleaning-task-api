import os
import jwt
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer()
JWT_SECRET = os.getenv("JWT_SECRET", "change-me-secret")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 12


def create_access_token(user_id: str, role: str) -> str:
    payload = {
        "user_id": user_id,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    token = credentials.credentials

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = payload.get("user_id")
        role = payload.get("role")

        if not user_id:
            raise HTTPException(status_code=401, detail="認証情報が不正です。")

        return {
            "user_id": user_id,
            "role": role,
        }

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="ログイン有効期限が切れています。")
    except Exception:
        raise HTTPException(status_code=401, detail="トークンが不正です。")


def get_current_user_id(current_user: dict = Depends(get_current_user)) -> str:
    return current_user["user_id"]


def require_admin_or_leader(current_user: dict = Depends(get_current_user)) -> dict:
    role = current_user.get("role")
    if role not in ["admin", "leader", "sub_admin", "operation", "payroll_admin"]:
        raise HTTPException(status_code=403, detail="管理画面にアクセスできません。")
    return current_user


def require_operational_write(current_user: dict = Depends(get_current_user)) -> dict:
    """タスク・設備・スケジュールなど日常運用機能の編集権限。"""
    role = current_user.get("role")
    if role not in ["admin", "sub_admin", "leader", "operation", "payroll_admin"]:
        raise HTTPException(status_code=403, detail="この操作を実行する権限がありません。")
    return current_user


def require_task_write(current_user: dict = Depends(get_current_user)) -> dict:
    """管理画面のタスク編集と、現場アカウントの担当タスク更新を許可する。"""
    role = current_user.get("role")
    allowed_roles = [
        "admin",
        "sub_admin",
        "leader",
        "operation",
        "payroll_admin",
        "staff",
        "checker",
        "contractor",
    ]
    if role not in allowed_roles:
        raise HTTPException(status_code=403, detail="タスクを更新する権限がありません。")
    return current_user


def require_admin_write(current_user: dict = Depends(get_current_user)) -> dict:
    """アカウント・物件・客室・割当マスタの編集権限。"""
    role = current_user.get("role")
    if role not in ["admin", "sub_admin"]:
        raise HTTPException(status_code=403, detail="この操作は管理者のみ実行できます。")
    return current_user


def require_shift_write(current_user: dict = Depends(get_current_user)) -> dict:
    """シフト表の編集権限。"""
    role = current_user.get("role")
    if role not in ["admin", "sub_admin"]:
        raise HTTPException(status_code=403, detail="シフト表は閲覧のみ可能です。")
    return current_user


def require_worklog_write(current_user: dict = Depends(get_current_user)) -> dict:
    """実働報告の修正・削除権限。"""
    role = current_user.get("role")
    if role not in ["admin", "sub_admin"]:
        raise HTTPException(status_code=403, detail="このアカウントは閲覧専用です。")
    return current_user


def require_payroll_access(
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """給与画面は leader を閲覧専用とし、更新は管理者と給与管理者だけに許可する。"""
    role = current_user.get("role")
    read_roles = ["admin", "sub_admin", "leader", "payroll_admin"]
    write_roles = ["admin", "sub_admin", "payroll_admin"]
    allowed_roles = read_roles if request.method == "GET" else write_roles
    if role not in allowed_roles:
        raise HTTPException(status_code=403, detail="給与・勤怠機能を利用する権限がありません。")
    return current_user
