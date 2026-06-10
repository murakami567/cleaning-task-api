import os
import time
from typing import Any

import jwt
import requests
from fastapi import HTTPException

from app.logger import get_logger

logger = get_logger(__name__)

BOT_ID = os.getenv("LINEWORKS_BOT_ID", "")
CLIENT_ID = os.getenv("LINEWORKS_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("LINEWORKS_CLIENT_SECRET", "")
SERVICE_ACCOUNT = os.getenv("LINEWORKS_SERVICE_ACCOUNT", "")
# Render の環境変数で改行を \n エスケープして格納される想定。
PRIVATE_KEY = os.getenv("LINEWORKS_PRIVATE_KEY", "").replace("\\n", "\n")

AUTH_URL = "https://auth.worksmobile.com/oauth2/v2.0/token"
API_BASE = "https://www.worksapis.com/v1.0"

# access_token は通常 24h 有効。安全側で 23h でキャッシュ切れにする。
_TOKEN_TTL_SEC = 23 * 60 * 60
_token_cache: dict[str, Any] = {"token": "", "expires_at": 0.0}


def _ensure_config():
    missing = []
    if not BOT_ID:
        missing.append("LINEWORKS_BOT_ID")
    if not CLIENT_ID:
        missing.append("LINEWORKS_CLIENT_ID")
    if not CLIENT_SECRET:
        missing.append("LINEWORKS_CLIENT_SECRET")
    if not SERVICE_ACCOUNT:
        missing.append("LINEWORKS_SERVICE_ACCOUNT")
    if not PRIVATE_KEY:
        missing.append("LINEWORKS_PRIVATE_KEY")
    if missing:
        raise HTTPException(
            status_code=500,
            detail=f"LINE WORKS 設定が不足: {', '.join(missing)}",
        )


def _build_jwt() -> str:
    now = int(time.time())
    payload = {
        "iss": CLIENT_ID,
        "sub": SERVICE_ACCOUNT,
        "iat": now,
        "exp": now + 3600,
    }
    return jwt.encode(payload, PRIVATE_KEY, algorithm="RS256")


def _get_access_token() -> str:
    """サービスアカウント JWT で access_token を取得。プロセス内キャッシュ。"""
    _ensure_config()

    now = time.time()
    if _token_cache["token"] and _token_cache["expires_at"] > now:
        return _token_cache["token"]

    try:
        jwt_token = _build_jwt()
    except Exception as e:
        # 一番ハマるのが秘密鍵の PEM 改行が崩れているケース。エラーメッセージに
        # 入っているとデバッグが早い。
        logger.error(
            f"lineworks JWT build failed: {e} "
            f"(private_key_starts_with={(PRIVATE_KEY or '')[:30]!r})",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=(
                "LINE WORKS の秘密鍵が読み込めません。"
                "Render の LINEWORKS_PRIVATE_KEY が PEM 形式 (改行込み) で"
                "設定されているか確認してください。"
            ),
        )

    try:
        res = requests.post(
            AUTH_URL,
            data={
                "assertion": jwt_token,
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "scope": "bot",
            },
            timeout=30,
        )
    except Exception as e:
        logger.error(f"lineworks token request failed: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail="LINE WORKS 認証リクエストに失敗しました。")

    if res.status_code != 200:
        logger.error(f"lineworks token returned {res.status_code}: {res.text[:300]}")
        raise HTTPException(status_code=502, detail=f"LINE WORKS 認証失敗: {res.status_code}")

    body = res.json() or {}
    token = body.get("access_token")
    if not token:
        logger.error(f"lineworks token missing in response: {body}")
        raise HTTPException(status_code=502, detail="LINE WORKS 認証レスポンスにトークンがありません。")

    _token_cache["token"] = token
    _token_cache["expires_at"] = now + _TOKEN_TTL_SEC
    return token


def send_text_to_channel(channel_id: str, text: str) -> None:
    """指定 channel_id のトークルームへ Bot がテキストメッセージを送る。"""
    if not channel_id:
        raise ValueError("channel_id is empty")
    if not text:
        raise ValueError("text is empty")

    token = _get_access_token()
    url = f"{API_BASE}/bots/{BOT_ID}/channels/{channel_id}/messages"

    body = {
        "content": {
            "type": "text",
            "text": text,
        }
    }

    try:
        res = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=30,
        )
    except Exception as e:
        logger.error(f"lineworks send failed: channel={channel_id} {e}", exc_info=True)
        raise

    if res.status_code == 401:
        # トークン期限切れ等。キャッシュ破棄して 1 度だけ再試行。
        _token_cache["token"] = ""
        _token_cache["expires_at"] = 0.0
        token = _get_access_token()
        res = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=30,
        )

    if not (200 <= res.status_code < 300):
        logger.error(
            f"lineworks send non-2xx: channel={channel_id} status={res.status_code} body={res.text[:300]}"
        )
        raise RuntimeError(f"LINE WORKS 送信失敗 (status={res.status_code})")
