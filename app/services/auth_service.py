import os
import jwt
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException, Depends
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
    if role not in ["admin", "leader"]:
        raise HTTPException(status_code=403, detail="管理画面にアクセスできません。")
    return current_user
