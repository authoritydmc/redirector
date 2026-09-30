"""Security utilities: password verification and bearer JWT token handling."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import jwt
from fastapi import Depends, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.core.config import settings
from backend.core.errors import AppError

security = HTTPBearer(auto_error=False)


def verify_password(plain_password: str, expected_password: str) -> bool:
    """Constant-time password comparison."""
    import secrets
    return secrets.compare_digest(plain_password, expected_password)


def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    """Generate encoded JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(minutes=settings.jwt_access_token_expire_minutes)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return encoded_jwt


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate JWT access token."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        return payload
    except jwt.PyJWTError:
        raise AppError(
            "Invalid credentials",
            status=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            code="auth:invalid-token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None


async def get_current_admin(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
) -> dict[str, Any]:
    """FastAPI dependency requiring valid admin JWT bearer token."""
    if not credentials or credentials.scheme.lower() != "bearer":
        raise AppError(
            "Authentication required",
            status=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            code="auth:required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_access_token(credentials.credentials)
    if payload.get("role") != "admin":
        raise AppError(
            "Admin privileges required",
            status=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
            code="auth:forbidden",
        )
    return payload
