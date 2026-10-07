"""Pydantic schemas for API keys."""

from pydantic import BaseModel, Field


class ApiKeyCreate(BaseModel):
    name: str = Field(..., description="Human label, e.g. 'nightly import cron'")
    scopes: list[str] = Field(
        default_factory=lambda: ["*"],
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
