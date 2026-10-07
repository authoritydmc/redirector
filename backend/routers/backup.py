"""Admin backup API under /api/v1/admin/backup (EPIC-04 task 7).

POST   ""               — enqueue a `backup_create` job (202 + job row)
POST   /{name}:restore  — enqueue a `backup_restore` job: safety backup
                         first, then merge-upsert by natural key (202)
GET    ""               — list archives, newest first
GET    /{name}          — download the zip (name strictly validated)
DELETE /{name}          — delete the archive
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import FileResponse

from backend.core.errors import AppError
from backend.core.security import ADMIN_READ, ADMIN_WRITE, RequireScopes
from backend.modules.backup.schemas import (
    BackupCreate,
    BackupDeleteResponse,
    BackupRead,
)
from backend.modules.backup.service import (
    BackupResult,
    backup_dir,
    backup_path,
    list_backups,
    sanitize_label,
)
from backend.modules.jobs.runner import JobRunner
from backend.modules.jobs.schemas import JobRead

router = APIRouter(prefix="/api/v1/admin/backup", tags=["admin-backup"])


def get_runner(request: Request) -> JobRunner:
    runner: JobRunner = request.app.state.jobs
    return runner


def _to_read(result: BackupResult) -> BackupRead:
    return BackupRead(
        name=result.name,
        size_bytes=result.size_bytes,
        created_at=result.created_at,
        tables=result.tables,
    )


def _resolve_or_404(name: str) -> Path:
    path = backup_path(backup_dir(), name)
    if path is None:
        raise AppError(
            "Backup not found",
            status=status.HTTP_404_NOT_FOUND,
            detail=f"Backup '{name}' not found",
            code="backup:not-found",
        )
    return path


@router.post("", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED,
             summary="Enqueue a backup job")
async def create_backup_job(
    body: BackupCreate,
    _admin: Annotated[dict[str, Any], Depends(RequireScopes(ADMIN_WRITE))],
    runner: Annotated[JobRunner, Depends(get_runner)],
) -> JobRead:
    label = sanitize_label(body.label)
    if body.label is not None and label is None:
        raise AppError(
            "Invalid backup label",
            status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Label must match [a-z0-9-] (max 32 chars)",
            code="backup:invalid-label",
        )
    job = await runner.enqueue("backup_create", {"label": label}, total=0)
    return JobRead.model_validate(job)


@router.get("", response_model=list[BackupRead], summary="List backups")
async def list_backup_archives(
    _admin: Annotated[dict[str, Any], Depends(RequireScopes(ADMIN_READ))],
) -> list[BackupRead]:
    return [_to_read(result) for result in list_backups(backup_dir())]


@router.post("/{name}:restore", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED,
             summary="Enqueue a restore job")
async def restore_backup_job(
    name: str,
    _admin: Annotated[dict[str, Any], Depends(RequireScopes(ADMIN_WRITE))],
    runner: Annotated[JobRunner, Depends(get_runner)],
) -> JobRead:
    if backup_path(backup_dir(), name) is None:
        raise AppError(
            "Backup not found",
            status=status.HTTP_404_NOT_FOUND,
            detail=f"Backup '{name}' not found",
            code="backup:not-found",
        )
    job = await runner.enqueue("backup_restore", {"name": name}, total=0)
    return JobRead.model_validate(job)


@router.get("/{name}", summary="Download a backup archive")
async def download_backup(
    name: str,
    _admin: Annotated[dict[str, Any], Depends(RequireScopes(ADMIN_READ))],
) -> FileResponse:
    path = _resolve_or_404(name)
    return FileResponse(path, filename=name, media_type="application/zip")


@router.delete("/{name}", response_model=BackupDeleteResponse, summary="Delete a backup")
async def delete_backup(
    name: str,
    _admin: Annotated[dict[str, Any], Depends(RequireScopes(ADMIN_WRITE))],
) -> BackupDeleteResponse:
    path = _resolve_or_404(name)
    try:
        path.unlink()
    except OSError:
        raise AppError(
            "Backup not found",
            status=status.HTTP_404_NOT_FOUND,
            detail=f"Backup '{name}' not found",
            code="backup:not-found",
        ) from None
    return BackupDeleteResponse(success=True, name=name)
