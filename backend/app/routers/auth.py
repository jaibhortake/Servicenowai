from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import auth
from app.config import settings

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(body: LoginRequest):
    if not settings.admin_password:
        raise HTTPException(503, "ADMIN_PASSWORD is not configured on the server")
    if not auth.verify_credentials(body.username, body.password):
        raise HTTPException(401, "Invalid username or password")
    return {
        "access_token": auth.create_token(body.username),
        "token_type": "bearer",
        "expires_in": settings.jwt_expire_minutes * 60,
        "user": {"username": body.username, "role": "admin"},
    }


@router.get("/me")
async def me(claims: dict = Depends(auth.require_user)):
    return {"username": claims.get("sub"), "role": claims.get("role")}
