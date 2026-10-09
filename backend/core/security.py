"""Security utilities: password verification and bearer JWT token handling."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import jwt
from fastapi import Depends, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import settings
from backend.core.db import get_session
from backend.core.errors import AppError
from backend.models.entities import AuthEvent, LoginAttempt
from backend.modules.auth.repository import ApiKeyRepository

security = HTTPBearer(auto_error=False)

# Scope vocabulary (EPIC-05 task 3, minimal): `*` implies everything and is
# what interactive JWT sessions carry. API keys carry a subset; routes
# declare what they need. Roles (owner/admin/editor/viewer) land later —
# scopes are the mechanism roles will map onto.
ADMIN_READ = "admin:read"
ADMIN_WRITE = "admin:write"


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
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """FastAPI dependency requiring admin identity: JWT bearer or API key.

    JWT (existing behavior, codes unchanged) is primary; `rk_*` bearer
    tokens verify as API keys and yield an equivalent admin identity with
    `auth_method` set (`jwt` vs `api_key`) so key-management endpoints can
    require an interactive session. Unknown/malformed tokens 401 either way.
    """
    if not credentials or credentials.scheme.lower() != "bearer":
        raise AppError(
            "Authentication required",
            status=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            code="auth:required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials
    if token.startswith("rk_"):
        row = await ApiKeyRepository(session).verify(token)
        if row is None:
            raise AppError(
                "Invalid credentials",
                status=status.HTTP_401_UNAUTHORIZED,
                detail="Unknown, revoked, or mismatched API key",
                code="auth:invalid-api-key",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return {
            "role": "admin",
            "sub": f"apikey:{row.id}",
            "auth_method": "api_key",
            "key_name": row.name,
            "scopes": row.scopes,
        }
    payload = decode_access_token(token)
    if payload.get("role") != "admin":
        raise AppError(
            "Admin privileges required",
            status=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
            code="auth:forbidden",
        )
    if "purpose" in payload:
        # Scoped tokens (e.g. mfa-pending) are only valid at their own
        # endpoint — never as general admin credentials.
        raise AppError(
            "Invalid credentials",
            status=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            code="auth:invalid-token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return {**payload, "auth_method": "jwt", "scopes": ["*"]}


class RequireScopes:
    """Dependency factory enforcing scopes on admin endpoints.

    Usage: `admin: Annotated[dict, Depends(RequireScopes("admin:read"))]`.
    JWT sessions carry `*` (imply everything); API keys must list every
    required scope. Denials are 403 `auth:insufficient-scope` (distinct
    from 401 unauthenticated and 403 non-admin, so automation can tell
    "bad key" from "narrow key" apart).
    """

    def __init__(self, *required: str) -> None:
        self.required = required

    async def __call__(
        self,
        admin: Annotated[dict[str, Any], Depends(get_current_admin)],
    ) -> dict[str, Any]:
        granted = admin.get("scopes") or []
        if "*" in granted or all(scope in granted for scope in self.required):
            return admin
        raise AppError(
            "Insufficient scope",
            status=status.HTTP_403_FORBIDDEN,
            detail=f"Endpoint requires scope(s): {', '.join(self.required)}",
            code="auth:insufficient-scope",
        )


def client_ip(request_ip: str | None, forwarded_for: str | None) -> str:
    """Client identity for rate limits and lockout: leftmost X-Forwarded-For
    when behind the documented proxy configs, else the peer address."""
    if forwarded_for:
        return forwarded_for.split(",")[0].strip().lower()
    return request_ip or "unknown"


async def log_auth_event(
    session: AsyncSession,
    kind: str,
    ip: str | None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Append one audit row (EPIC-05 task 11)."""
    session.add(AuthEvent(kind=kind, detail=detail or {}, ip=ip))
    await session.commit()


async def failed_logins_since(
    session: AsyncSession, ip: str, window_minutes: int
) -> int:
    """Count failures inside the lockout window (prunes older rows first)."""
    cutoff = datetime.now(UTC) - timedelta(minutes=window_minutes)
    await session.execute(
        delete(LoginAttempt).where(LoginAttempt.attempted_at < cutoff))
    count = (await session.execute(
        select(func.count()).select_from(LoginAttempt).where(LoginAttempt.ip == ip)
    )).scalar() or 0
    await session.commit()
    return int(count)


async def record_failed_login(session: AsyncSession, ip: str) -> None:
    """Persist one failed attempt (lockout accounting)."""
    session.add(LoginAttempt(ip=ip))
    await session.commit()
