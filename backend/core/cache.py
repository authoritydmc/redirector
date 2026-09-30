"""Cache abstraction (EPIC-04/06). Routes/services depend on the Protocol,
never on Redis directly. Implementations: MemoryCache (dev/tests/M1),
RedisCache (EPIC-04, drop-in via same interface).
"""

from __future__ import annotations

import time
from typing import Protocol


class Cache(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl: int = 300) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def clear_prefix(self, prefix: str) -> int: ...


class MemoryCache:
    """Thread-safe-enough in-memory cache with TTL. No I/O, no threads."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[str, float | None]] = {}

    async def get(self, key: str) -> str | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires = entry
        if expires is not None and expires <= time.monotonic():
            self._store.pop(key, None)
            return None
        return value

    async def set(self, key: str, value: str, ttl: int = 300) -> None:
        self._store[key] = (value, time.monotonic() + ttl if ttl > 0 else None)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def clear_prefix(self, prefix: str) -> int:
        doomed = [k for k in self._store if k.startswith(prefix)]
        for k in doomed:
            del self._store[k]
        return len(doomed)
