"""FastAPI router for authentication and admin session issuance."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import resolve_setting, settings
from backend.core.db import get_session
from backend.core.errors import AppError
from backend.core.ratelimit import RateLimit
from backend.core.security import (
    client_ip,
    create_access_token,
    decode_access_token,
    failed_logins_since,
    get_current_admin,
    log_auth_event,
    record_failed_login,
    require_jwt_session,
    verify_password,
)
from backend.models.entities import ApiKey, User
from backend.modules.auth.repository import (
    ApiKeyRepository,
    MfaRepository,
    generate_api_key,
    hash_secret,
    new_backup_codes,
    new_totp_seed,
    verify_secret,
    verify_totp,
)
from backend.modules.auth.schemas import (
    ApiKeyCreate,
    ApiKeyIssued,
    ApiKeyRead,
    MfaChallengeResponse,
    MfaDisableRequest,
    MfaEnableRequest,
    MfaEnableResponse,
    MfaRegenerateRequest,
    MfaSetupResponse,
    MfaStatusResponse,
    MfaVerifyRequest,
)
from backend.modules.teams.repository import UserRepository, normalize_email
from backend.modules.teams.schemas import UserLogin, UserRead, UserSignup

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# Brute-force budgets (threat model): per-route buckets, 5/min/IP.
# Team-account endpoints share their own bucket (separate credential
# surface from the admin password — attackers get no double budget
# either way, and each surface keeps the full 5/min for legit use).
login_limiter = RateLimit("5/minute")
user_limiter = RateLimit("5/minute")
verify_limiter = RateLimit("5/minute")


async def _lockout_window(session: AsyncSession) -> int:
    value, _ = await resolve_setting(session, "auth_lockout_window_minutes")
    return int(value)


async def _lockout_max(session: AsyncSession) -> int:
    value, _ = await resolve_setting(session, "auth_lockout_max_attempts")
    return int(value)


async def _token_ttl(session: AsyncSession) -> int:
    value, _ = await resolve_setting(session, "jwt_access_token_expire_minutes")
    return int(value)


class LoginRequest(BaseModel):
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str


class CurrentUserResponse(BaseModel):
    role: str
    sub: str


@router.post("/login", response_model=TokenResponse | MfaChallengeResponse, summary="Admin login")
async def login(
    request: Request,
    body: LoginRequest,
    mfa_repo: Annotated[MfaRepository, Depends(get_mfa_repo)],
    session: Annotated[AsyncSession, Depends(get_session)],
    _rate_limited: Annotated[None, Depends(login_limiter)],
) -> TokenResponse | MfaChallengeResponse:
    """Authenticate admin password: JWT immediately, or an MFA challenge
    (exchange at `mfa/verify`) when TOTP is enrolled."""
    ip = client_ip(
        request.client.host if request.client else None,
        request.headers.get("x-forwarded-for"),
    )
    failures = await failed_logins_since(
        session, ip, await _lockout_window(session))
    if failures >= await _lockout_max(session):
        await log_auth_event(session, "auth.locked-out", ip,
                             {"failures": failures})
        raise AppError(
            "Account locked",
            status=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed logins; try again later",
            code="auth:locked-out",
        )
    if not verify_password(body.password, settings.admin_password):
        await record_failed_login(session, ip)
        await log_auth_event(session, "login.failed", ip)
        raise AppError(
            "Invalid credentials",
            status=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect password",
            code="auth:bad-credentials",
        )
    await log_auth_event(session, "login.success", ip)
    if await mfa_repo.is_enabled():
        pending = create_access_token(
            data={"sub": "admin", "role": "admin", "purpose": "mfa-pending"},
            expires_delta=timedelta(minutes=5),
        )
        return MfaChallengeResponse(pending_token=pending)
    token = create_access_token(
        data={"sub": "admin", "role": "admin"}, ttl_minutes=await _token_ttl(session))
    return TokenResponse(access_token=token, role="admin")


@router.get("/me", response_model=CurrentUserResponse, summary="Current admin")
async def get_me(admin: Annotated[dict[str, Any], Depends(get_current_admin)]) -> CurrentUserResponse:
    """Return currently authenticated identity."""
    return CurrentUserResponse(role=admin["role"], sub=admin["sub"])


def require_admin_session(admin: dict[str, Any]) -> None:
    """Admin-only management over an interactive session: API keys (even
    wide ones) and non-admin users never qualify, so leaked automation
    credentials can't rewire access."""
    require_jwt_session(admin)
    if admin.get("role") != "admin":
        raise AppError(
            "Admin privileges required",
            status=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
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


async def get_mfa_repo(session: AsyncSession = Depends(get_session)) -> MfaRepository:
    return MfaRepository(session)


@router.post("/api-keys", response_model=ApiKeyIssued, status_code=status.HTTP_201_CREATED,
             summary="Issue an API key")
async def issue_api_key(
    body: ApiKeyCreate,
    admin: Annotated[dict[str, Any], Depends(get_current_admin)],
    repo: Annotated[ApiKeyRepository, Depends(_get_repo)],
) -> ApiKeyIssued:
    """Mint a key for automation. The plaintext is returned exactly once."""
    require_admin_session(admin)
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
    require_admin_session(admin)
    return [_to_read(row) for row in await repo.list_all()]


@router.delete("/api-keys/{key_id}", response_model=ApiKeyRead, summary="Revoke an API key")
async def revoke_api_key(
    key_id: int,
    admin: Annotated[dict[str, Any], Depends(get_current_admin)],
    repo: Annotated[ApiKeyRepository, Depends(_get_repo)],
) -> ApiKeyRead:
    require_admin_session(admin)
    row = await repo.get_by_id(key_id)
    if row is None:
        raise AppError(
            "API key not found",
            status=status.HTTP_404_NOT_FOUND,
            detail=f"API key id {key_id} not found",
            code="auth:key-not-found",
        )
    return _to_read(await repo.revoke(row))


def _to_user_read(row: User) -> UserRead:
    return UserRead(
        id=row.id,  # type: ignore[arg-type]
        email=row.email,
        name=row.name,
        disabled=bool(row.disabled),
        created_at=row.created_at.isoformat(),
    )


async def _get_users(session: AsyncSession = Depends(get_session)) -> UserRepository:
    return UserRepository(session)


@router.post("/signup", response_model=UserRead, status_code=status.HTTP_201_CREATED,
             summary="Sign up a team account")
async def signup(
    body: UserSignup,
    request: Request,
    repo: Annotated[UserRepository, Depends(_get_users)],
    session: Annotated[AsyncSession, Depends(get_session)],
    _rate_limited: Annotated[None, Depends(user_limiter)],
) -> UserRead:
    """Open registration for team accounts (self-hosted: disable abuse
    accounts from Admin). Never reveals whether an email exists beyond
    the 409 on re-signup."""
    email = normalize_email(body.email)
    if email is None:
        raise AppError(
            "Invalid email",
            status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Enter a valid email address",
            code="users:invalid-email",
        )
    if len(body.password) < 8:
        raise AppError(
            "Weak password",
            status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Password needs at least 8 characters",
            code="users:weak-password",
        )
    if await repo.get_by_email(email) is not None:
        raise AppError(
            "Account exists",
            status=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists — sign in instead",
            code="users:exists",
        )
    row = await repo.create(email, body.name, hash_secret(body.password))
    await log_auth_event(session, "user.signup", _ip_of(request), {"user_id": row.id})
    return _to_user_read(row)


@router.post("/user/login", response_model=TokenResponse, summary="Team account login")
async def user_login(
    body: UserLogin,
    request: Request,
    repo: Annotated[UserRepository, Depends(_get_users)],
    session: Annotated[AsyncSession, Depends(get_session)],
    _rate_limited: Annotated[None, Depends(user_limiter)],
) -> TokenResponse:
    """Password login for team accounts. Same lockout accounting as the
    admin login; disabled accounts fail closed without oracle differences
    beyond the 403 (deliberate: admins must tell disabled users apart)."""
    email = normalize_email(body.email) or ""
    ip = client_ip(
        request.client.host if request.client else None,
        request.headers.get("x-forwarded-for"),
    )
    failures = await failed_logins_since(session, ip, await _lockout_window(session))
    if failures >= await _lockout_max(session):
        await log_auth_event(session, "auth.locked-out", ip, {"failures": failures})
        raise AppError(
            "Account locked",
            status=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed logins; try again later",
            code="auth:locked-out",
        )
    row = await repo.get_by_email(email)
    if row is None or not verify_secret(body.password, row.password_hash):
        await record_failed_login(session, ip)
        await log_auth_event(session, "login.failed", ip)
        raise AppError(
            "Invalid credentials",
            status=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            code="auth:bad-credentials",
        )
    if row.disabled:
        await log_auth_event(session, "user.login_blocked", ip, {"user_id": row.id})
        raise AppError(
            "Account disabled",
            status=status.HTTP_403_FORBIDDEN,
            detail="This account is disabled — ask an admin",
            code="users:disabled",
        )
    await log_auth_event(session, "user.login", ip, {"user_id": row.id})
    token = create_access_token(
        data={"sub": row.email, "role": "user", "scopes": []},
        ttl_minutes=await _token_ttl(session))
    return TokenResponse(access_token=token, role="user")


@router.get("/users", response_model=list[UserRead], summary="List team accounts")
async def list_users(
    admin: Annotated[dict[str, Any], Depends(get_current_admin)],
    repo: Annotated[UserRepository, Depends(_get_users)],
) -> list[UserRead]:
    """Admin roster (never password material)."""
    require_admin_session(admin)
    return [_to_user_read(row) for row in await repo.list_all()]


@router.post("/users/{email}/disable", response_model=UserRead, summary="Disable a team account")
async def disable_user(
    email: str,
    admin: Annotated[dict[str, Any], Depends(get_current_admin)],
    repo: Annotated[UserRepository, Depends(_get_users)],
) -> UserRead:
    require_admin_session(admin)
    row = await repo.get_by_email(normalize_email(email) or "")
    if row is None:
        raise AppError(
            "User not found",
            status=status.HTTP_404_NOT_FOUND,
            detail=f"No account with email {email}",
            code="users:not-found",
        )
    return _to_user_read(await repo.set_disabled(row, True))


@router.post("/users/{email}/enable", response_model=UserRead, summary="Re-enable a team account")
async def enable_user(
    email: str,
    admin: Annotated[dict[str, Any], Depends(get_current_admin)],
    repo: Annotated[UserRepository, Depends(_get_users)],
) -> UserRead:
    require_admin_session(admin)
    row = await repo.get_by_email(normalize_email(email) or "")
    if row is None:
        raise AppError(
            "User not found",
            status=status.HTTP_404_NOT_FOUND,
            detail=f"No account with email {email}",
            code="users:not-found",
        )
    return _to_user_read(await repo.set_disabled(row, False))


def _ip_of(request: Request) -> str | None:
    host = request.client.host if request.client else None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip().lower()
    return host


def _mfa_token_or_401(secret: str | None, token: str) -> None:
    if secret is None or not verify_totp(secret, token):
        raise AppError(
            "Invalid credentials",
            status=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect TOTP token",
            code="auth:bad-totp",
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.get("/mfa/status", response_model=MfaStatusResponse, summary="MFA enrollment status")
async def mfa_status(
    admin: Annotated[dict[str, Any], Depends(get_current_admin)],
    repo: Annotated[MfaRepository, Depends(get_mfa_repo)],
) -> MfaStatusResponse:
    require_admin_session(admin)
    return MfaStatusResponse(enabled=await repo.is_enabled(),
                             backup_codes_remaining=await repo.code_count())


@router.post("/mfa/setup", response_model=MfaSetupResponse, summary="Start TOTP enrollment")
async def mfa_setup(
    admin: Annotated[dict[str, Any], Depends(get_current_admin)],
    repo: Annotated[MfaRepository, Depends(get_mfa_repo)],
) -> MfaSetupResponse:
    """Stage a fresh seed (render the URL as QR). Nothing is enabled until
    a token from the new seed verifies at `mfa/enable`."""
    require_admin_session(admin)
    if await repo.is_enabled():
        raise AppError(
            "MFA already enrolled",
            status=status.HTTP_409_CONFLICT,
            detail="Disable MFA before starting a new enrollment",
            code="auth:mfa-enrolled",
        )
    secret, otpauth_url = new_totp_seed()
    await repo.start_setup(secret)
    return MfaSetupResponse(otpauth_url=otpauth_url, secret=secret)


@router.post("/mfa/enable", response_model=MfaEnableResponse, summary="Enable TOTP")
async def mfa_enable(
    body: MfaEnableRequest,
    admin: Annotated[dict[str, Any], Depends(get_current_admin)],
    repo: Annotated[MfaRepository, Depends(get_mfa_repo)],
) -> MfaEnableResponse:
    """Verify a token from the staged seed: enables MFA and mints single-use
    backup codes (plaintext exactly once — store them now)."""
    require_admin_session(admin)
    if await repo.is_enabled():
        raise AppError(
            "MFA already enrolled",
            status=status.HTTP_409_CONFLICT,
            detail="Disable MFA before starting a new enrollment",
            code="auth:mfa-enrolled",
        )
    secret = await repo.get_secret()
    if secret is None:
        raise AppError(
            "MFA setup required",
            status=status.HTTP_409_CONFLICT,
            detail="Run mfa/setup before enabling",
            code="auth:mfa-setup-required",
        )
    _mfa_token_or_401(secret, body.token)
    plaintext, hashes = new_backup_codes()
    await repo.enable(hashes)
    return MfaEnableResponse(backup_codes=plaintext)


@router.post("/mfa/disable", response_model=MfaStatusResponse, summary="Disable MFA")
async def mfa_disable(
    body: MfaDisableRequest,
    admin: Annotated[dict[str, Any], Depends(get_current_admin)],
    repo: Annotated[MfaRepository, Depends(get_mfa_repo)],
) -> MfaStatusResponse:
    """Disable MFA with TOTP proof of possession; clears seed and codes."""
    require_admin_session(admin)
    if not await repo.is_enabled():
        raise AppError(
            "MFA not enrolled",
            status=status.HTTP_409_CONFLICT,
            detail="Nothing to disable",
            code="auth:mfa-not-enrolled",
        )
    _mfa_token_or_401(await repo.get_secret(), body.token)
    await repo.disable()
    return MfaStatusResponse(enabled=False, backup_codes_remaining=0)


@router.post("/mfa/verify", response_model=TokenResponse, summary="Complete MFA login")
async def mfa_verify(
    request: Request,
    body: MfaVerifyRequest,
    repo: Annotated[MfaRepository, Depends(get_mfa_repo)],
    _rate_limited: Annotated[None, Depends(verify_limiter)],
) -> TokenResponse:
    """Exchange a pending login token + TOTP (or one unused backup code,
    consumed on use) for a full JWT."""
    try:
        pending = decode_access_token(body.pending_token)
    except AppError:
        raise AppError(
            "Invalid credentials",
            status=status.HTTP_401_UNAUTHORIZED,
            detail="Expired or invalid pending token — log in again",
            code="auth:bad-pending-token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    if pending.get("purpose") != "mfa-pending" or pending.get("role") != "admin":
        raise AppError(
            "Invalid credentials",
            status=status.HTTP_401_UNAUTHORIZED,
            detail="Not a pending MFA token",
            code="auth:bad-pending-token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not await repo.is_enabled():
        raise AppError(
            "MFA not enrolled",
            status=status.HTTP_400_BAD_REQUEST,
            detail="MFA is not enabled; log in directly",
            code="auth:mfa-not-enrolled",
        )
    secret = await repo.get_secret()
    if secret is not None and verify_totp(secret, body.token):
        token = create_access_token(
            data={"sub": "admin", "role": "admin"}, ttl_minutes=await _token_ttl(repo.session))
        return TokenResponse(access_token=token, role="admin")
    if await repo.consume_backup_code(body.token):
        token = create_access_token(
            data={"sub": "admin", "role": "admin"}, ttl_minutes=await _token_ttl(repo.session))
        return TokenResponse(access_token=token, role="admin")
    await log_auth_event(
        repo.session,
        "mfa.verify_failed",
        client_ip(request.client.host if request.client else None,
                  request.headers.get("x-forwarded-for")),
    )
    raise AppError(
        "Invalid credentials",
        status=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect TOTP token or spent backup code",
        code="auth:bad-totp",
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.post("/mfa/backup-codes:regenerate", response_model=MfaEnableResponse,
             summary="Regenerate backup codes")
async def mfa_regenerate_codes(
    body: MfaRegenerateRequest,
    admin: Annotated[dict[str, Any], Depends(get_current_admin)],
    repo: Annotated[MfaRepository, Depends(get_mfa_repo)],
) -> MfaEnableResponse:
    """Mint a fresh backup-code set (old codes die) with TOTP possession proof."""
    require_admin_session(admin)
    if not await repo.is_enabled():
        raise AppError(
            "MFA not enrolled",
            status=status.HTTP_409_CONFLICT,
            detail="Enable MFA before regenerating codes",
            code="auth:mfa-not-enrolled",
        )
    _mfa_token_or_401(await repo.get_secret(), body.token)
    plaintext, hashes = new_backup_codes()
    await repo.enable(hashes)
    return MfaEnableResponse(backup_codes=plaintext)
