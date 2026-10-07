"""API key persistence + crypto (EPIC-05 task 4).

Key format: `rk_<prefix>_<secret>` (12 + 43 urlsafe chars — fixed widths so
parsing never depends on alphabet separators). Only sha256(secret) is
stored; verification is constant-time. Revocation is a timestamp, so
`last_used_at` history survives.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.entities import ApiKey

_KEY_TAG = "rk_"
_PREFIX_LEN = 12
_SECRET_LEN = 43  # token_urlsafe(32)


def generate_api_key() -> tuple[str, str, str]:
    """Return (display key, prefix, secret) for a fresh issuance."""
    prefix = secrets.token_urlsafe(9)
    assert len(prefix) == _PREFIX_LEN, "prefix width must stay fixed for parsing"
    secret = secrets.token_urlsafe(32)
    assert len(secret) == _SECRET_LEN, "secret width must stay fixed for parsing"
    return f"{_KEY_TAG}{prefix}_{secret}", prefix, secret


def parse_api_key(token: str) -> tuple[str, str] | None:
    """Split a display key into (prefix, secret); None when malformed."""
    if not token.startswith(_KEY_TAG):
        return None
    body = token[len(_KEY_TAG):]
    if len(body) != _PREFIX_LEN + 1 + _SECRET_LEN or body[_PREFIX_LEN] != "_":
        return None
    return body[:_PREFIX_LEN], body[_PREFIX_LEN + 1:]


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


class ApiKeyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, name: str, scopes: list[str], secret_hash: str, prefix: str) -> ApiKey:
        row = ApiKey(name=name, scopes=scopes, secret_hash=secret_hash, prefix=prefix)
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def list_all(self) -> list[ApiKey]:
        rows = await self.session.execute(
            select(ApiKey).order_by(ApiKey.__table__.c.id.desc()))
        return list(rows.scalars().all())

    async def get_by_id(self, key_id: int) -> ApiKey | None:
        return (await self.session.execute(
            select(ApiKey).where(ApiKey.id == key_id))).scalar_one_or_none()

    async def get_by_prefix(self, prefix: str) -> ApiKey | None:
        return (await self.session.execute(
            select(ApiKey).where(ApiKey.prefix == prefix))).scalar_one_or_none()

    async def revoke(self, row: ApiKey) -> ApiKey:
        row.revoked_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def verify(self, token: str) -> ApiKey | None:
        """Return the live row for a valid key, else None (no oracle)."""
        parsed = parse_api_key(token)
        if parsed is None:
            return None
        prefix, secret = parsed
        row = await self.get_by_prefix(prefix)
        if row is None or row.revoked_at is not None:
            return None
        if not secrets.compare_digest(row.secret_hash, hash_secret(secret)):
            return None
        row.last_used_at = datetime.now(UTC)
        await self.session.commit()
        return row
