"""Redirect resolution service — pure port of v2 handle_redirect rules.

v2 behavior preserved (app/routes/redirection_routes.py + app/utils/utils.py):
- lowercase + slash-trimmed normalization; longest-prefix segment match
- static requires an exact match (extra segments fall through)
- legacy fallback for stored patterns containing `{`/`[` (base-prefix match)
- dynamic/user-dynamic positional substitution over deduped [square]+{curly}
- dynamic: all placeholders required; user-dynamic: only UserParam.required
- access_count incremented on every served redirect (not on upstream hits)
- only http/https targets served; SSO targets get no-store (router concern)
- unknown subpath → suggestions (prefix-ranked, shortest first)

Auth context (is_admin/current_user) is accepted but unauthenticated for now —
EPIC-05 wires real identity; the gates already behave correctly for anon.
"""

from __future__ import annotations

import re
import time
from urllib.parse import urlparse

from backend.models.entities import (
    Shortcut,
    ShortcutType,
    UpstreamCache,
    Visibility,
)
from backend.modules.shortcuts.repository import (
    ShortcutRepository,
    is_sso_url,
    normalize_subpath,
    sanitize_pattern,
)
from backend.modules.shortcuts.schemas import Resolution, ShortcutRead

_UNSAFE_SCHEMES = ("javascript:", "data:", "vbscript:", "file:", "blob:")


def is_safe_redirect_target(url: str | None) -> bool:
    """Port of v2 is_safe_redirect_target: absolute http/https only."""
    if not url or not isinstance(url, str):
        return False
    url = url.strip()
    if url.lower().startswith(_UNSAFE_SCHEMES):
        return False
    try:
        return urlparse(url).scheme.lower() in ("http", "https")
    except Exception:
        return False


def get_placeholder_vars(target: str) -> list[str]:
    """Port of v2 get_placeholder_vars: [square] first, then {curly}."""
    square = re.findall(r"\[([^\]]+)\]", target)
    curly = re.findall(r"\{([^}]+)\}", target)
    return square + curly if square or curly else []


def _to_read(row: Shortcut) -> ShortcutRead:
    return ShortcutRead(
        id=row.id, pattern=row.pattern, type=row.type, target=row.target,
        access_count=row.access_count or 0, tags=row.tags or [],
        visibility=row.visibility,
        expires_at=row.expires_at.isoformat() if row.expires_at else None,
        owner_email=row.owner_email,
    )


def _visible(row: Shortcut, *, is_admin: bool,
             client_ip: str | None, current_user: str | None) -> bool:
    vis = row.visibility or Visibility.PUBLIC
    if vis in (Visibility.PUBLIC, Visibility.UNLISTED):
        return True
    if vis == Visibility.PRIVATE:
        if is_admin:
            return True
        owner = row.owner_email or row.created_ip
        if owner and (owner == current_user or owner == client_ip):
            return True
        return owner is None  # legacy rows without owner stay reachable
    if vis == Visibility.TEAM:
        return bool(is_admin or (row.owner_email and row.owner_email == current_user))
    return True


async def _suggestions(repo: ShortcutRepository, query: str) -> list[ShortcutRead]:
    return [_to_read(r) for r in await repo.find_similar(query)]


async def resolve(
    subpath: str | None,
    repo: ShortcutRepository,
    *,
    countdown_delay: int = 1,
    client_ip: str | None = None,
    current_user: str | None = None,
    is_admin: bool = False,
) -> Resolution:
    start = time.monotonic()
    elapsed = lambda: round(time.monotonic() - start, 6)  # noqa: E731
    sanitized = normalize_subpath(subpath)
    if not sanitized:
        return Resolution(outcome="not_found", elapsed=elapsed())

    segments = sanitized.split("/")
    matched: Shortcut | UpstreamCache | None = None
    source = ""
    matched_pattern = ""
    remaining: list[str] = []

    for i in range(len(segments), 0, -1):
        candidate = "/".join(segments[:i])
        entity, src = await repo.lookup(candidate)
        if entity is None:
            continue
        stype = entity.type if isinstance(entity, Shortcut) else ShortcutType.STATIC
        rest = segments[i:]
        if stype == ShortcutType.STATIC and rest:
            continue  # static needs an exact match; try a shorter prefix
        matched, source, matched_pattern, remaining = entity, src, candidate, rest
        break

    if matched is None:
        # Legacy fallback: stored pattern literally contains { or [ placeholders.
        for row in await repo.list_dynamic():
            base = sanitize_pattern((row.pattern or "").split("{")[0].split("[")[0])
            if not base:
                continue
            if sanitized == base or sanitized.startswith(base + "/"):
                tail = sanitized[len(base):].strip("/")
                matched, source = row, "db"
                matched_pattern = row.pattern
                remaining = tail.split("/") if tail else []
                break

    if matched is None:
        return Resolution(outcome="not_found", elapsed=elapsed(),
                          suggestions=await _suggestions(repo, sanitized))

    if isinstance(matched, UpstreamCache):
        target = matched.resolved_url or ""
        if not is_safe_redirect_target(target):
            return Resolution(outcome="unsafe", pattern=matched_pattern, elapsed=elapsed())
        return Resolution(outcome="redirect", pattern=matched_pattern, target=target,
                          source=source, elapsed=elapsed(), countdown_delay=countdown_delay)

    if matched.is_expired():
        return Resolution(outcome="gone", pattern=matched_pattern, elapsed=elapsed(),
                          suggestions=await _suggestions(repo, sanitized))
    if not _visible(matched, is_admin=is_admin, client_ip=client_ip, current_user=current_user):
        return Resolution(outcome="forbidden", pattern=matched_pattern, elapsed=elapsed())

    if matched.type == ShortcutType.STATIC:
        if not is_safe_redirect_target(matched.target):
            return Resolution(outcome="unsafe", pattern=matched_pattern, elapsed=elapsed())
        await repo.increment_access(matched_pattern)
        return Resolution(outcome="redirect", pattern=matched_pattern, target=matched.target,
                          source=source, elapsed=elapsed(), countdown_delay=countdown_delay)

    # Dynamic / user-dynamic: positional substitution.
    placeholders = list(dict.fromkeys(get_placeholder_vars(matched.target or "")))
    param_values = {name: remaining[i] for i, name in enumerate(placeholders)
                    if i < len(remaining)}
    if matched.type == ShortcutType.USER_DYNAMIC:
        required = {p.param_name for p in await repo.get_user_params(matched_pattern)
                    if p.required}
        missing = [n for n in placeholders if n in required and not param_values.get(n)]
    else:
        missing = [n for n in placeholders if not param_values.get(n)]
    if missing:
        return Resolution(outcome="need_params", pattern=matched_pattern, elapsed=elapsed(),
                          placeholders=placeholders, param_values=param_values,
                          missing_params=missing)
    dest = matched.target or ""
    for name in placeholders:
        dest = dest.replace("{" + name + "}", param_values.get(name, ""))
        dest = dest.replace("[" + name + "]", param_values.get(name, ""))
    if not is_safe_redirect_target(dest):
        return Resolution(outcome="unsafe", pattern=matched_pattern, elapsed=elapsed())
    await repo.increment_access(matched_pattern)
    _ = is_sso_url(dest)  # router adds no-store headers when True; kept explicit here
    return Resolution(outcome="redirect", pattern=matched_pattern, target=dest,
                      source=source, elapsed=elapsed(), countdown_delay=countdown_delay)
