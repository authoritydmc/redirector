"""Pydantic schemas for Upstreams and Upstream Cache."""

from pydantic import BaseModel, Field


class UpstreamBase(BaseModel):
    name: str = Field(..., description="Unique label for the upstream")
    base_url: str = Field(..., description="Target base URL, e.g. https://go.dev")
    fail_url: str | None = Field(default=None, description="URL returned by upstream on 404")
    fail_status_code: int | None = Field(default=None, description="Status code indicating missing shortcut")
    verify_ssl: bool = Field(default=True, description="Verify SSL certs")
    skip_sso_cache: bool = Field(default=False, description="Never cache SSO target matches")


class UpstreamCreate(UpstreamBase):
    pass


class UpstreamUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    fail_url: str | None = None
    fail_status_code: int | None = None
    verify_ssl: bool | None = None
    skip_sso_cache: bool | None = None


class UpstreamRead(UpstreamBase):
    id: int | None = None

    model_config = {"from_attributes": True}


class UpstreamCheckResult(BaseModel):
    upstream_name: str
    check_url: str
    status: str  # found | sso_required | not_found | error | skipped
    target_url: str | None = None
    status_code: int | None = None
    cached: bool = False
    message: str = ""


class UpstreamCacheEntry(BaseModel):
    pattern: str
    upstream_name: str
    resolved_url: str | None = None
    checked_at: str

    model_config = {"from_attributes": True}


class CacheResyncRequest(BaseModel):
    upstream: str = Field(..., description="Upstream name to resync against")
    pattern: str | None = Field(
        default=None,
        description="Single pattern to refresh; omit to re-check all cached patterns",
    )


class CacheResyncResult(BaseModel):
    pattern: str
    success: bool
    status: str  # found | sso_required | not_found | error | skipped
    resolved_url: str | None = None
    message: str = ""


class CacheResyncResponse(BaseModel):
    success: bool
    upstream: str
    checked: int
    updated: int
    cleared: int
    results: list[CacheResyncResult]


class CheckLogEntry(BaseModel):
    id: int | None = None
    pattern: str
    upstream_name: str
    check_url: str | None = None
    result: str | None = None
    detail: str | None = None
    tried_at: str
    count: int = 1
    cached: bool = False


class CachePurgeResult(BaseModel):
    success: bool
    purged: int
