"""Pydantic schemas for admin backups."""

from pydantic import BaseModel, Field


class BackupCreate(BaseModel):
    label: str | None = Field(
        default=None,
        description="Optional filename tag, [a-z0-9-] max 32 chars",
    )


class BackupRead(BaseModel):
    name: str
    size_bytes: int
    created_at: str
    tables: dict[str, int]


class BackupDeleteResponse(BaseModel):
    success: bool
    name: str
