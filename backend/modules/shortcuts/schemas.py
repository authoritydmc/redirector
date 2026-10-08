"""API schemas for the resolve flow (EPIC-03 error/pagination standards apply
to list endpoints; resolve uses outcome + RFC 7807 problems)."""

from pydantic import BaseModel, Field

from backend.models.entities import ShortcutType, Visibility


class ShortcutRead(BaseModel):
    id: int | None = None
    pattern: str
    type: ShortcutType
    target: str
    access_count: int = 0
    tags: list[str] = Field(default_factory=list)
    visibility: Visibility = Visibility.PUBLIC
    expires_at: str | None = None
    owner_email: str | None = None

    model_config = {"from_attributes": True}


class ShortcutCreate(BaseModel):
    pattern: str
    target: str
    type: ShortcutType = ShortcutType.STATIC
    tags: list[str] = Field(default_factory=list)
    visibility: Visibility = Visibility.PUBLIC
    expires_at: str | None = None
    owner_email: str | None = None


class ShortcutUpdate(BaseModel):
    target: str | None = None
    type: ShortcutType | None = None
    tags: list[str] | None = None
    visibility: Visibility | None = None
    expires_at: str | None = None
    owner_email: str | None = None


class ShortcutListMeta(BaseModel):
    page: int
    pageSize: int
    total: int


class ShortcutListResponse(BaseModel):
    data: list[ShortcutRead]
    meta: ShortcutListMeta


class BulkDeleteRequest(BaseModel):
    patterns: list[str]


class BulkDeleteResponse(BaseModel):
    deleted: list[str]
    not_found: list[str]
    count: int


class Resolution(BaseModel):
    """Outcome of resolving a subpath. `outcome` drives client behavior."""

    outcome: str  # redirect | need_params | not_found | gone | forbidden | unsafe
    pattern: str = ""
    target: str | None = None
    source: str | None = None  # memory | db | upstream
    elapsed: float = 0.0
    countdown_delay: int = 0
    placeholders: list[str] = Field(default_factory=list)
    param_values: dict[str, str] = Field(default_factory=dict)
    missing_params: list[str] = Field(default_factory=list)
    suggestions: list[ShortcutRead] = Field(default_factory=list)


class Problem(BaseModel):
    """RFC 7807 error envelope."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str = ""
    code: str = ""
    suggestions: list[ShortcutRead] = Field(default_factory=list)
    missing_params: list[str] = Field(default_factory=list)
