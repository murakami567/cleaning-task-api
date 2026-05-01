from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.db import supabase
from app.services.auth_service import create_access_token

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    login_id: str
    password: str
    role: str | None = None


@router.post("/login")
def login(payload: LoginRequest):
    res = (
        supabase
        .table("staff_members")
        .select("*")
        .eq("staff_code", payload.login_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )

    if not res.data:
        raise HTTPException(status_code=401, detail="ユーザーが存在しません。")

    user = res.data[0]
    user_role = user.get("role")

    if user.get("password") != payload.password:
        raise HTTPException(status_code=401, detail="パスワードが違います。")

    # 管理画面ログイン
    if payload.role == "admin_portal":
        if user_role not in ["admin", "leader"]:
            raise HTTPException(status_code=403, detail="管理画面にログインできません。")

    # 一般画面ログイン：staff_members に存在し、is_active=true、パスワード一致なら全員OK
    elif payload.role == "employee_portal":
        pass

    # 個別ロール指定が来た場合
    elif payload.role:
        if user_role != payload.role:
            raise HTTPException(status_code=403, detail="この画面にログインできません。")

    access_token = create_access_token(str(user["id"]), user_role or "")

    return {
        "access_token": access_token,
        "user": {
            "id": user.get("id"),
            "name": user.get("staff_name"),
            "login_id": user.get("staff_code"),
            "role": user_role,
        },
    }
