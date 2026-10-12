"""Team router: team + roster management for normal users (usernames epic).

Every endpoint needs an interactive JWT session (password users,
SSO admins, password admin) — automation keys stay out, mirroring key
management. Site admins (role admin) bypass membership; everyone else
acts through their membership row (team-admin manages the roster).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.db import get_session
from backend.core.errors import AppError
from backend.core.security import caller_email, get_current_admin, require_jwt_session
from backend.models.entities import Team
from backend.modules.teams.repository import TeamRepository, UserRepository, normalize_email
from backend.modules.teams.schemas import (
    TeamCreate,
    TeamMemberAdd,
    TeamMemberRead,
    TeamRead,
)

router = APIRouter(prefix="/api/v1/teams", tags=["teams"])


async def _session(
    admin: Annotated[dict[str, Any], Depends(get_current_admin)],
) -> dict[str, Any]:
    require_jwt_session(admin)
    return admin


async def _repo(session: AsyncSession = Depends(get_session)) -> TeamRepository:
    return TeamRepository(session)


def _to_read(team: Team, members: list[TeamMemberRead] | None = None) -> TeamRead:
    return TeamRead(
        id=team.id,  # type: ignore[arg-type]
        name=team.name,
        created_by_email=team.created_by_email,
        created_at=team.created_at.isoformat(),
        members=members if members is not None else [],
    )


async def _team_or_404(repo: TeamRepository, team_id: int) -> Team:
    team = await repo.get(team_id)
    if team is None:
        raise AppError(
            "Team not found",
            status=status.HTTP_404_NOT_FOUND,
            detail=f"No team with id {team_id}",
            code="teams:not-found",
        )
    return team


async def _membership_role(
    repo: TeamRepository, team: Team, me: dict[str, Any], session: AsyncSession
) -> str | None:
    """Site admins get `"site-admin"`; members their row role; outsiders
    (and email-less identities) get None — callers turn that into 404
    (no oracle for team existence)."""
    if me.get("role") == "admin":
        return "site-admin"
    email = await caller_email(me, session)
    if email is None:
        return None
    team_id = team.id
    assert team_id is not None
    return await repo.member_role(team_id, email)


@router.post("", response_model=TeamRead, status_code=status.HTTP_201_CREATED,
             summary="Create a team")
async def create_team(
    body: TeamCreate,
    me: Annotated[dict[str, Any], Depends(_session)],
    session: Annotated[AsyncSession, Depends(get_session)],
    repo: Annotated[TeamRepository, Depends(_repo)],
) -> TeamRead:
    """Any signed-in user can found a team (and becomes its first
    team-admin). Names are unique install-wide."""
    name = body.name.strip()
    if not name:
        raise AppError(
            "Invalid team name",
            status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Team name must not be blank",
            code="teams:invalid-name",
        )
    if await repo.get_by_name(name) is not None:
        raise AppError(
            "Team exists",
            status=status.HTTP_409_CONFLICT,
            detail=f"A team named {name!r} already exists",
            code="teams:exists",
        )
    email = await caller_email(me, session)
    team = await repo.create(name, email)
    return _to_read(team, await _member_reads(repo, team))


@router.get("", response_model=list[TeamRead], summary="List teams")
async def list_teams(
    me: Annotated[dict[str, Any], Depends(_session)],
    session: Annotated[AsyncSession, Depends(get_session)],
    repo: Annotated[TeamRepository, Depends(_repo)],
) -> list[TeamRead]:
    """Site admins see everything; everyone else sees their memberships."""
    if me.get("role") == "admin":
        return [_to_read(team) for team in await repo.list_all()]
    email = await caller_email(me, session)
    if email is None:
        return []
    mine = await repo.team_ids_for(email)
    return [_to_read(team) for team in await repo.list_all() if team.id in mine]


@router.get("/{team_id}", response_model=TeamRead, summary="Team roster")
async def get_team(
    team_id: int,
    me: Annotated[dict[str, Any], Depends(_session)],
    session: Annotated[AsyncSession, Depends(get_session)],
    repo: Annotated[TeamRepository, Depends(_repo)],
) -> TeamRead:
    team = await _team_or_404(repo, team_id)
    if await _membership_role(repo, team, me, session) is None:
        raise AppError(
            "Team not found",
            status=status.HTTP_404_NOT_FOUND,
            detail=f"No team with id {team_id}",
            code="teams:not-found",
        )
    return _to_read(team, await _member_reads(repo, team))


@router.delete("/{team_id}", response_model=TeamRead, summary="Delete a team")
async def delete_team(
    team_id: int,
    me: Annotated[dict[str, Any], Depends(_session)],
    session: Annotated[AsyncSession, Depends(get_session)],
    repo: Annotated[TeamRepository, Depends(_repo)],
) -> TeamRead:
    team = await _team_or_404(repo, team_id)
    role = await _membership_role(repo, team, me, session)
    if role not in ("site-admin", "admin"):
        raise AppError(
            "Team not found",
            status=status.HTTP_404_NOT_FOUND,
            detail=f"No team with id {team_id}",
            code="teams:not-found",
        )
    if await repo.link_count(team_id) > 0:
        raise AppError(
            "Team still has links",
            status=status.HTTP_409_CONFLICT,
            detail="Move or delete the team's links before deleting it",
            code="teams:links-exist",
        )
    members = await _member_reads(repo, team)
    deleted = _to_read(team, members)
    await repo.delete(team)
    return deleted


@router.post("/{team_id}/members", response_model=TeamMemberRead,
             status_code=status.HTTP_201_CREATED, summary="Add a team member")
async def add_member(
    team_id: int,
    body: TeamMemberAdd,
    me: Annotated[dict[str, Any], Depends(_session)],
    session: Annotated[AsyncSession, Depends(get_session)],
    repo: Annotated[TeamRepository, Depends(_repo)],
) -> TeamMemberRead:
    team = await _team_or_404(repo, team_id)
    role = await _membership_role(repo, team, me, session)
    if role not in ("site-admin", "admin"):
        raise AppError(
            "Team not found",
            status=status.HTTP_404_NOT_FOUND,
            detail=f"No team with id {team_id}",
            code="teams:not-found",
        )
    email = normalize_email(body.email)
    if email is None:
        raise AppError(
            "Invalid email",
            status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Enter a valid email address",
            code="users:invalid-email",
        )
    if body.role not in ("member", "admin"):
        raise AppError(
            "Invalid member role",
            status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Role must be member or admin",
            code="teams:invalid-role",
        )
    if not await _known_email(session, email):
        raise AppError(
            "Unknown account",
            status=status.HTTP_404_NOT_FOUND,
            detail=f"No account with email {email} — they sign up first",
            code="users:not-found",
        )
    team_id_value = team.id
    assert team_id_value is not None
    if await repo.member_role(team_id_value, email) is not None:
        raise AppError(
            "Already a member",
            status=status.HTTP_409_CONFLICT,
            detail=f"{email} is already on this team",
            code="teams:member-exists",
        )
    row = await repo.add_member(team_id_value, email, body.role)
    return TeamMemberRead(email=row.email, role=row.role,
                          created_at=row.created_at.isoformat())


@router.delete("/{team_id}/members/{email}", response_model=TeamMemberRead,
               summary="Remove a team member")
async def remove_member(
    team_id: int,
    email: str,
    me: Annotated[dict[str, Any], Depends(_session)],
    session: Annotated[AsyncSession, Depends(get_session)],
    repo: Annotated[TeamRepository, Depends(_repo)],
) -> TeamMemberRead:
    team = await _team_or_404(repo, team_id)
    role = await _membership_role(repo, team, me, session)
    if role not in ("site-admin", "admin"):
        raise AppError(
            "Team not found",
            status=status.HTTP_404_NOT_FOUND,
            detail=f"No team with id {team_id}",
            code="teams:not-found",
        )
    target = normalize_email(email) or ""
    team_id_value = team.id
    assert team_id_value is not None
    current = await repo.member_role(team_id_value, target)
    if current is None:
        raise AppError(
            "Team not found",
            status=status.HTTP_404_NOT_FOUND,
            detail=f"No team with id {team_id}",
            code="teams:not-found",
        )
    if current == "admin" and await repo.admin_count(team_id_value) <= 1:
        raise AppError(
            "Last admin",
            status=status.HTTP_409_CONFLICT,
            detail="A team always keeps one admin — promote someone first",
            code="teams:last-admin",
        )
    removed = [m for m in await repo.members(team_id_value) if m.email == target]
    assert removed
    stamped = removed[0].created_at.isoformat()
    await repo.remove_member(team_id_value, target)
    return TeamMemberRead(email=target, role=current, created_at=stamped)


async def _member_reads(repo: TeamRepository, team: Team) -> list[TeamMemberRead]:
    team_id = team.id
    assert team_id is not None
    return [TeamMemberRead(email=m.email, role=m.role, created_at=m.created_at.isoformat())
            for m in await repo.members(team_id)]


async def _known_email(session: AsyncSession, email: str) -> bool:
    """Registered as a local user or an SSO identity (either may join)."""
    if await UserRepository(session).get_by_email(email) is not None:
        return True
    from backend.modules.teams.repository import TeamRepository as _Teams

    return await _Teams(session).known_sso_email(email)
