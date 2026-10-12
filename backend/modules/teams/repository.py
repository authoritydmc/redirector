"""Persistence for local users, teams, and membership."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.entities import Shortcut, SsoUser, Team, TeamMember, User

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(raw: str) -> str | None:
    """Lowercased email, or None when it is not shaped like one."""
    email = raw.strip().lower()
    return email if _EMAIL_RE.match(email) else None


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_email(self, email: str) -> User | None:
        return (await self.session.execute(
            select(User).where(User.email == email))).scalar_one_or_none()

    async def create(self, email: str, name: str | None, password_hash: str) -> User:
        row = User(email=email, name=(name or "").strip() or None,
                   password_hash=password_hash)
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def list_all(self) -> list[User]:
        rows = await self.session.execute(select(User).order_by(User.email))
        return list(rows.scalars().all())

    async def set_disabled(self, row: User, disabled: bool) -> User:
        row.disabled = disabled
        await self.session.commit()
        await self.session.refresh(row)
        return row


class TeamRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, team_id: int) -> Team | None:
        return (await self.session.execute(
            select(Team).where(Team.id == team_id))).scalar_one_or_none()

    async def get_by_name(self, name: str) -> Team | None:
        return (await self.session.execute(
            select(Team).where(Team.name == name))).scalar_one_or_none()

    async def create(self, name: str, creator_email: str | None) -> Team:
        row = Team(name=name.strip(), created_by_email=creator_email)
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        if creator_email:
            await self.add_member(row.id, creator_email, "admin")
        return row

    async def list_all(self) -> list[Team]:
        rows = await self.session.execute(select(Team).order_by(Team.name))
        return list(rows.scalars().all())

    async def delete(self, row: Team) -> None:
        await self.session.delete(row)
        await self.session.commit()

    async def members(self, team_id: int) -> list[TeamMember]:
        rows = await self.session.execute(
            select(TeamMember).where(TeamMember.team_id == team_id).order_by(TeamMember.email))
        return list(rows.scalars().all())

    async def member_role(self, team_id: int, email: str) -> str | None:
        row = (await self.session.execute(
            select(TeamMember).where(
                TeamMember.team_id == team_id, TeamMember.email == email)
        )).scalar_one_or_none()
        return row.role if row else None

    async def add_member(self, team_id: int, email: str, role: str) -> TeamMember:
        row = TeamMember(team_id=team_id, email=email, role=role)
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def remove_member(self, team_id: int, email: str) -> bool:
        row = (await self.session.execute(
            select(TeamMember).where(
                TeamMember.team_id == team_id, TeamMember.email == email)
        )).scalar_one_or_none()
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.commit()
        return True

    async def admin_count(self, team_id: int) -> int:
        count = (await self.session.execute(
            select(func.count()).select_from(TeamMember).where(
                TeamMember.team_id == team_id, TeamMember.role == "admin")
        )).scalar() or 0
        return int(count)

    async def team_ids_for(self, email: str) -> set[int]:
        rows = await self.session.execute(
            select(TeamMember).where(TeamMember.email == email))
        return {row.team_id for row in rows.scalars().all()}

    async def link_count(self, team_id: int) -> int:
        count = (await self.session.execute(
            select(func.count()).select_from(Shortcut).where(Shortcut.team_id == team_id)
        )).scalar() or 0
        return int(count)

    async def known_sso_email(self, email: str) -> bool:
        """An SSO identity already carries this email (invitable)."""
        row = (await self.session.execute(
            select(SsoUser).where(SsoUser.email == email))).scalar_one_or_none()
        return row is not None
