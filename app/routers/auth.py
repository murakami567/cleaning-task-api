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
        .table("users")
        .select("*")
        .eq("login_id", payload.login_id)
        .limit(1)
        .execute()
    )

    if not res.data:
        raise HTTPException(status_code=401, detail="ユーザーが存在しません。")

    user = res.data[0]

    if user.get("password") != payload.password:
        raise HTTPException(status_code=401, detail="パスワードが違います。")

    if payload.role and user.get("role") != payload.role:
        raise HTTPException(status_code=403, detail="この画面にログインできません。")

    access_token = create_access_token(str(user["id"]))

    return {
        "access_token": access_token,
        "user": {
            "id": user.get("id"),
            "name": user.get("name"),
            "login_id": user.get("login_id"),
            "role": user.get("role"),
        },
    }
