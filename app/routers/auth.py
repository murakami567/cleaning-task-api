from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import supabase
from app.logger import get_logger
from app.services.auth_service import create_access_token

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = get_logger(__name__)


class LoginRequest(BaseModel):
    login_id: str
    password: str
    role: str | None = None


@router.post("/login")
def login(payload: LoginRequest):
    try:
        res = (
            supabase
            .table("staff_members")
            .select("*")
            .eq("staff_code", payload.login_id)
            .eq("is_active", True)
            .limit(1)
            .execute()
        )
    except Exception as e:
        logger.error(f"login DB error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="ログイン処理に失敗しました。")

    if not res.data:
        logger.warning(f"login failed: user not found login_id={payload.login_id}")
        raise HTTPException(status_code=401, detail="ユーザーが存在しません。")

    user = res.data[0]
    user_role = user.get("role")

    if user.get("password") != payload.password:
        logger.warning(f"login failed: wrong password login_id={payload.login_id}")
        raise HTTPException(status_code=401, detail="パスワードが違います。")

    if payload.role == "admin_portal":
        if user_role not in ["admin", "leader", "sub_admin"]:
            logger.warning(
                f"login failed: insufficient role login_id={payload.login_id} role={user_role}"
            )
            raise HTTPException(status_code=403, detail="管理画面にログインできません。")
    elif payload.role == "employee_portal":
        pass
    elif payload.role:
        if user_role != payload.role:
            logger.warning(
                f"login failed: role mismatch login_id={payload.login_id} role={user_role}"
            )
            raise HTTPException(status_code=403, detail="この画面にログインできません。")

    access_token = create_access_token(str(user["id"]), user_role or "")
    logger.info(f"login success: login_id={payload.login_id} role={user_role}")

    return {
        "access_token": access_token,
        "user": {
            "id": user.get("id"),
            "name": user.get("staff_name"),
            "login_id": user.get("staff_code"),
            "role": user_role,
        },
    }
