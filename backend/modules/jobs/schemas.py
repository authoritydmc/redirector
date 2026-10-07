"""Pydantic schemas for background jobs."""

from typing import Literal

from pydantic import BaseModel, Field

from backend.models.entities import JSONValue


class UpstreamResyncEnqueue(BaseModel):
    kind: Literal["upstream_resync"] = Field(description="Job kind to run")
    upstream: str = Field(..., description="Upstream name to resync against")
    patterns: list[str] = Field(..., min_length=1, description="Patterns to re-check")


class BackupEnqueue(BaseModel):
    kind: Literal["backup_create"] = Field(description="Job kind to run")
    label: str | None = Field(default=None, description="Optional filename tag")


JobEnqueue = UpstreamResyncEnqueue | BackupEnqueue


class JobRead(BaseModel):
    id: int
    kind: str
    status: str
    total: int = 0
    done: int = 0
    result: dict[str, JSONValue] | None = None
    error: str | None = None

    model_config = {"from_attributes": True}
