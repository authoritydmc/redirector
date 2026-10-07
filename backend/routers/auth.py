"""FastAPI router for authentication and admin session issuance."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import settings
from backend.core.db import get_session
from backend.core.errors import AppError
from backend.core.security import create_access_token, get_current_admin, verify_password
from backend.models.entities import ApiKey
from backend.modules.auth.repository import ApiKeyRepository, generate_api_key, hash_secret
from backend.modules.auth.schemas import ApiKeyCreate, ApiKeyIssued, ApiKeyRead

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


def _require_jwt_session(admin: dict[str, Any]) -> None:
    """Key management needs an interactive session: a leaked key must not be
    able to mint or revoke sibling keys."""
    if admin.get("auth_method") != "jwt":
        raise AppError(
            "Admin session required",
            status=status.HTTP_403_FORBIDDEN,
            detail="API key management requires an interactive JWT session",
            code="auth:forbidden",
        )


def _to_read(row: ApiKey) -> ApiKeyRead:
    return ApiKeyRead(
        id=row.id,  # type: ignore[arg-type]
        prefix=row.prefix,
        name=row.name,
        scopes=row.scopes,
        created_at=row.created_at.isoformat(),
        last_used_at=row.last_used_at.isoformat() if row.last_used_at else None,
        revoked_at=row.revoked_at.isoformat() if row.revoked_at else None,
    )


async def _get_repo(session: AsyncSession = Depends(get_session)) -> ApiKeyRepository:
    return ApiKeyRepository(session)


@router.post("/api-keys", response_model=ApiKeyIssued, status_code=status.HTTP_201_CREATED,
             summary="Issue an API key")
async def issue_api_key(
    body: ApiKeyCreate,
    admin: Annotated[dict[str, Any], Depends(get_current_admin)],
    repo: Annotated[ApiKeyRepository, Depends(_get_repo)],
) -> ApiKeyIssued:
    """Mint a key for automation. The plaintext is returned exactly once."""
    _require_jwt_session(admin)
    name = body.name.strip()
    if not name:
        raise AppError(
            "Invalid key name",
            status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Key name must not be blank",
            code="auth:invalid-name",
        )
    display, prefix, secret = generate_api_key()
    row = await repo.create(name, body.scopes, hash_secret(secret), prefix)
    issued = _to_read(row)
    return ApiKeyIssued(**issued.model_dump(), api_key=display)


@router.get("/api-keys", response_model=list[ApiKeyRead], summary="List API keys")
async def list_api_keys(
    admin: Annotated[dict[str, Any], Depends(get_current_admin)],
    repo: Annotated[ApiKeyRepository, Depends(_get_repo)],
) -> list[ApiKeyRead]:
    """List keys (never secret material — only prefixes)."""
    _require_jwt_session(admin)
    return [_to_read(row) for row in await repo.list_all()]


@router.delete("/api-keys/{key_id}", response_model=ApiKeyRead, summary="Revoke an API key")
async def revoke_api_key(
    key_id: int,
    admin: Annotated[dict[str, Any], Depends(get_current_admin)],
    repo: Annotated[ApiKeyRepository, Depends(_get_repo)],
) -> ApiKeyRead:
    _require_jwt_session(admin)
    row = await repo.get_by_id(key_id)
    if row is None:
        raise AppError(
            "API key not found",
            status=status.HTTP_404_NOT_FOUND,
            detail=f"API key id {key_id} not found",
            code="auth:key-not-found",
        )
    return _to_read(await repo.revoke(row))
