"""Pydantic schemas for API keys."""

from pydantic import BaseModel, Field


class ApiKeyCreate(BaseModel):
    name: str = Field(..., description="Human label, e.g. 'nightly import cron'")
    scopes: list[str] = Field(
        default_factory=lambda: ["*"],
        min_length=1,
        description="Recorded now, enforced by the RBAC audit (EPIC-05 task 3)",
    )


class ApiKeyRead(BaseModel):
    id: int
    prefix: str
    name: str
    scopes: list[str] = []
    created_at: str
    last_used_at: str | None = None
    revoked_at: str | None = None


class ApiKeyIssued(ApiKeyRead):
    """Issuance response — carries the plaintext key exactly once."""

    api_key: str


class MfaSetupResponse(BaseModel):
    """Fresh TOTP seed: render `otpauth_url` as QR, keep `secret` for manual
    entry. Nothing is enabled until a token verifies (see enable)."""

    otpauth_url: str
    secret: str


class MfaEnableRequest(BaseModel):
    token: str = Field(..., description="Current 6-digit TOTP from the new seed")


class MfaEnableResponse(BaseModel):
    enabled: bool = True
    backup_codes: list[str] = Field(
        ..., description="Single-use codes, plaintext exactly once — store them now")


class MfaVerifyRequest(BaseModel):
    pending_token: str = Field(..., description="Short-lived login token from a challenged login")
    token: str = Field(..., description="Current TOTP or one unused backup code")


class MfaStatusResponse(BaseModel):
    enabled: bool
    backup_codes_remaining: int


class MfaRegenerateRequest(BaseModel):
    token: str = Field(..., description="Current TOTP proving possession")


class MfaDisableRequest(BaseModel):
    token: str = Field(..., description="Current TOTP confirming the disable")


class MfaChallengeResponse(BaseModel):
    """Challenged login: no JWT yet — exchange `pending_token` + TOTP at
    `POST /api/v1/auth/mfa/verify`."""

    mfa_required: bool = True
    pending_token: str
