"""FastAPI router for authentication and admin session issuance."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from backend.core.config import settings
from backend.core.errors import AppError
from backend.core.security import create_access_token, get_current_admin, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str


class CurrentUserResponse(BaseModel):
    role: str
    sub: str


@router.post("/login", response_model=TokenResponse, summary="Admin login")
async def login(request: LoginRequest) -> TokenResponse:
    """Authenticate admin password and return JWT access token."""
    if not verify_password(request.password, settings.admin_password):
        raise AppError(
            "Invalid credentials",
            status=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect password",
            code="auth:bad-credentials",
        )

    token = create_access_token(data={"sub": "admin", "role": "admin"})
    return TokenResponse(access_token=token, role="admin")


@router.get("/me", response_model=CurrentUserResponse, summary="Current admin")
async def get_me(admin: Annotated[dict[str, Any], Depends(get_current_admin)]) -> CurrentUserResponse:
    """Return currently authenticated identity."""
    return CurrentUserResponse(role=admin["role"], sub=admin["sub"])
