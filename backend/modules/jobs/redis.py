"""Redis connection settings for the arq broker (EPIC-06 task 2).

Parses `REDIRECTOR_REDIS_URL` (`redis://[[user]:pass@]host:port/db`,
`rediss://...` for TLS) into arq's `RedisSettings`. Pure parsing — no
connections are opened here.
"""

from __future__ import annotations

from urllib.parse import unquote, urlparse

from arq.connections import RedisSettings


def redis_settings_from_url(url: str) -> RedisSettings:
    parsed = urlparse(url)
    if parsed.scheme not in ("redis", "rediss"):
        raise ValueError(f"REDIS_URL must start with redis:// or rediss://, got: {url!r}")
    database = 0
    if parsed.path and parsed.path != "/":
        try:
            database = int(parsed.path.lstrip("/"))
        except ValueError:
            raise ValueError(f"REDIS_URL database must be a number, got: {url!r}") from None
    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=database,
        username=unquote(parsed.username) if parsed.username else None,
        password=unquote(parsed.password) if parsed.password else None,
        ssl=parsed.scheme == "rediss",
    )
