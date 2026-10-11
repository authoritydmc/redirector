"""Persistence for SSO identities and in-flight OIDC logins."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.entities import OidcState, SsoUser, as_utc

OIDC_STATE_TTL = timedelta(minutes=10)


class SsoUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, provider: str, subject: str) -> SsoUser | None:
        return (await self.session.execute(
            select(SsoUser).where(
                SsoUser.provider == provider, SsoUser.subject == subject)
        )).scalar_one_or_none()

    async def upsert_login(
        self,
        provider: str,
        subject: str,
        email: str | None,
        name: str | None,
        role: str,
    ) -> SsoUser:
        """JIT-provision on first sight, refresh profile + role on return."""
        row = await self.get(provider, subject)
        now = datetime.now(UTC)
        if row is None:
            row = SsoUser(provider=provider, subject=subject, email=email,
                          name=name, role=role, last_login_at=now)
            self.session.add(row)
        else:
            row.email = email
            row.name = name
            row.role = role
            row.last_login_at = now
        await self.session.commit()
        await self.session.refresh(row)
        return row


class OidcStateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, state: str, provider: str, code_verifier: str, nonce: str) -> OidcState:
        now = datetime.now(UTC)
        row = OidcState(state=state, provider=provider, code_verifier=code_verifier,
                        nonce=nonce, created_at=now, expires_at=now + OIDC_STATE_TTL)
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def consume(self, state: str, provider: str) -> OidcState | None:
        """One-shot fetch: deletes the row when valid; prunes expired ones.
        Returns None for unknown, foreign-provider, or expired states."""
        await self.session.execute(
            delete(OidcState).where(OidcState.expires_at < datetime.now(UTC)))
        row = (await self.session.execute(
            select(OidcState).where(OidcState.state == state))).scalar_one_or_none()
        if row is None:
            await self.session.commit()
            return None
        await self.session.delete(row)
        await self.session.commit()
        if row.provider != provider:
            return None
        if (as_utc(row.expires_at) or datetime.min.replace(tzinfo=UTC)) < datetime.now(UTC):
            return None
        return row
