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
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.entities import ApiKey, Setting

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


def new_totp_seed() -> tuple[str, str]:
    """Fresh TOTP seed plus its otpauth URL (render as QR client-side)."""
    import pyotp

    secret = pyotp.random_base32()
    url = pyotp.totp.TOTP(secret).provisioning_uri(name="admin", issuer_name="redirector")
    return secret, url


def verify_totp(secret: str, token: str) -> bool:
    """Check a 6-digit token with one step of clock-skew tolerance."""
    import pyotp

    try:
        return bool(pyotp.TOTP(secret).verify(token.strip(), valid_window=1))
    except Exception:
        return False


def new_backup_codes(count: int = 10) -> tuple[list[str], list[str]]:
    """Fresh single-use codes; returns (plaintext, sha256 hashes)."""
    plaintext = [secrets.token_hex(4) for _ in range(count)]
    return plaintext, [hash_secret(code) for code in plaintext]


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


class MfaRepository:
    """TOTP enrollment state in the settings table (EPIC-05 task 5, TOTP half).

    Keys live under `mfa.*` (dotted convention, cf. import-v2). The TOTP
    seed sits here as an interim measure — secrets-vault migration (task 10)
    moves it out; backup codes are sha256 from day one. WebAuthn is a
    separate slice and has no storage yet.
    """

    ENABLED_KEY = "mfa.enabled"
    SECRET_KEY = "mfa.secret"
    CODES_KEY = "mfa.backup_codes"

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _get(self, key: str) -> Any:
        row = (await self.session.execute(
            select(Setting).where(Setting.key == key))).scalar_one_or_none()
        return row.value if row is not None else None

    async def _set(self, key: str, value: Any) -> None:
        row = (await self.session.execute(
            select(Setting).where(Setting.key == key))).scalar_one_or_none()
        if row is None:
            self.session.add(Setting(key=key, value=value))
        else:
            row.value = value
            row.updated_at = datetime.now(UTC)
        await self.session.commit()

    async def is_enabled(self) -> bool:
        return bool(await self._get(self.ENABLED_KEY))

    async def get_secret(self) -> str | None:
        secret = await self._get(self.SECRET_KEY)
        return secret if isinstance(secret, str) and secret else None

    async def start_setup(self, secret: str) -> None:
        """Stage a fresh seed; enrollment completes on first valid token."""
        await self._set(self.SECRET_KEY, secret)
        await self._set(self.ENABLED_KEY, False)

    async def enable(self, code_hashes: list[str]) -> None:
        await self._set(self.CODES_KEY, code_hashes)
        await self._set(self.ENABLED_KEY, True)

    async def disable(self) -> None:
        await self._set(self.SECRET_KEY, None)
        await self._set(self.CODES_KEY, [])
        await self._set(self.ENABLED_KEY, False)

    async def code_count(self) -> int:
        codes = await self._get(self.CODES_KEY)
        return len(codes) if isinstance(codes, list) else 0

    async def consume_backup_code(self, code: str) -> bool:
        """Single-use: match by hash, drop it, report whether one matched."""
        codes = await self._get(self.CODES_KEY)
        if not isinstance(codes, list):
            return False
        digest = hash_secret(code.strip())
        remaining = [c for c in codes
                     if not (isinstance(c, str)
                             and secrets.compare_digest(c, digest))]
        if len(remaining) == len(codes):
            return False
        await self._set(self.CODES_KEY, remaining)
        return True
