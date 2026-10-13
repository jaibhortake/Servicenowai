"""Single-admin JWT authentication (stdlib hashing + PyJWT)."""

import hashlib
import hmac
import secrets
import time
from typing import Optional

import jwt
from fastapi import HTTPException, status
from starlette.requests import HTTPConnection

from app.config import settings

_ALGO = "HS256"
_SALT = secrets.token_bytes(16)


def _digest(password: str) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), _SALT, 120_000)


# The configured password is hashed once in memory so it is never compared in plain text.
_ADMIN_DIGEST = _digest(settings.admin_password) if settings.admin_password else None


def verify_credentials(username: str, password: str) -> bool:
    if _ADMIN_DIGEST is None:
        return False
    user_ok = hmac.compare_digest(username or "", settings.admin_username)
    pass_ok = hmac.compare_digest(_digest(password or ""), _ADMIN_DIGEST)
    return user_ok and pass_ok


def create_token(username: str) -> str:
    now = int(time.time())
    payload = {"sub": username, "role": "admin", "iat": now, "exp": now + settings.jwt_expire_minutes * 60}
    return jwt.encode(payload, settings.app_secret_key, algorithm=_ALGO)


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.app_secret_key, algorithms=[_ALGO])
    except jwt.PyJWTError:
        return None


def _extract_token(conn: HTTPConnection) -> str:
    header = conn.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return conn.query_params.get("token", "")  # websockets cannot set headers from browsers


async def require_user(conn: HTTPConnection) -> dict:
    """Dependency for HTTP and WebSocket routes."""
    if not settings.auth_enabled:
        return {"sub": settings.admin_username, "role": "admin"}
    claims = decode_token(_extract_token(conn))
    if not claims:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    return claims


async def require_admin(conn: HTTPConnection) -> dict:
    claims = await require_user(conn)
    if claims.get("role") != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin only")
    return claims
