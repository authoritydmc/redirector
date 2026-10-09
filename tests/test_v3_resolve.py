"""Resolve-service tests (EPIC-01 hot path) with a fake in-memory repository.

No threads, no sockets, no DB — pure coroutines, so these run inside the
shared pytest process even with v2's gevent patching active.
Skipped when SQLModel isn't installed (main-branch CI).
"""

from datetime import timedelta

import pytest

pytest.importorskip("sqlmodel")

from backend.models.entities import (  # noqa: E402
    Shortcut,
    ShortcutType,
    UpstreamCache,
    UserParam,
    Visibility,
    utcnow,
)
from backend.modules.shortcuts.service import (  # noqa: E402
    get_placeholder_vars,
    is_safe_redirect_target,
    resolve,
)

pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend():
    return "asyncio"


class FakeRepo:
    def __init__(self, shortcuts=(), cache=(), params=()):
        self.by_pattern = {s.pattern.lower(): s for s in shortcuts}
        self.cache = {(c.pattern, c.upstream_name): c for c in cache}
        self.params = list(params)
        self.increments = []

    async def lookup(self, pattern):
        if pattern in self.by_pattern:
            return self.by_pattern[pattern], "db"
        for (p, _), c in self.cache.items():
            if p == pattern:
                return c, "upstream"
        return None, ""

    async def increment_access(self, pattern):
        self.increments.append(pattern)
        row = self.by_pattern.get(pattern)
        if row is not None:
            row.access_count = (row.access_count or 0) + 1

    async def list_dynamic(self):
        return [s for s in self.by_pattern.values()
                if s.type in (ShortcutType.DYNAMIC, ShortcutType.USER_DYNAMIC)]

    async def find_similar(self, query, limit=3):
        q = query.strip().lower()
        hits = [s for s in self.by_pattern.values() if q in s.pattern.lower()]
        return sorted(hits, key=lambda s: len(s.pattern))[:limit]

    async def get_user_params(self, pattern):
        return [p for p in self.params if p.shortcut_pattern == pattern]


def S(pattern, target="https://x.example", **kw):
    return Shortcut(pattern=pattern, target=target, **kw)


async def test_static_exact_redirect_and_count():
    repo = FakeRepo([S("docs", "https://x.example/docs")])
    res = await resolve("docs", repo, countdown_delay=0)
    assert (res.outcome, res.target, res.source) == ("redirect", "https://x.example/docs", "db")
    assert repo.increments == ["docs"]


async def test_normalization_case_and_slashes():
    repo = FakeRepo([S("docs", "https://x.example/docs")])
    assert (await resolve("  /DOCS/ ", repo)).outcome == "redirect"
    assert (await resolve("", repo)).outcome == "not_found"
    assert (await resolve(None, repo)).outcome == "not_found"


async def test_static_needs_exact_match():
    repo = FakeRepo([S("docs")])
    res = await resolve("docs/extra/segments", repo)
    assert res.outcome == "not_found"


async def test_hierarchical_prefix_match():
    repo = FakeRepo([S("x/abc"), S("x")])
    assert (await resolve("x/abc", repo)).pattern == "x/abc"
    # shorter prefix matches exactly → redirect to "x"
    res = await resolve("x", repo)
    assert res.outcome == "redirect" and res.pattern == "x"
    # static never matches with leftover segments, at any prefix depth (v2 parity)
    res2 = await resolve("x/abc/deep", repo)
    assert res2.outcome == "not_found"


async def test_dynamic_substitution_and_missing():
    repo = FakeRepo([S("jira", "https://j.example/browse/{ticket}", type=ShortcutType.DYNAMIC)])
    res = await resolve("jira/PROJ-1", repo)
    # v2 parity: the whole subpath is lowercased before matching, so values
    # inherit lowercasing (patterns are case-insensitive by design).
    assert res.outcome == "redirect" and res.target == "https://j.example/browse/proj-1"
    need = await resolve("jira", repo)
    assert need.outcome == "need_params" and need.missing_params == ["ticket"]


async def test_user_dynamic_required_params():
    repo = FakeRepo(
        [S("gh", "https://github.com/[user]", type=ShortcutType.USER_DYNAMIC)],
        params=[UserParam(shortcut_pattern="gh", param_name="user",
                          description="GitHub handle", required=True)],
    )
    need = await resolve("gh", repo)
    assert need.outcome == "need_params"
    res = await resolve("gh/octocat", repo)
    assert res.outcome == "redirect" and res.target == "https://github.com/octocat"


async def test_legacy_brace_pattern_fallback():
    repo = FakeRepo([S("redir-dyn/{foo}", "https://x.example/{foo}", type=ShortcutType.DYNAMIC)])
    res = await resolve("redir-dyn/bar", repo)
    assert res.outcome == "redirect" and res.target == "https://x.example/bar"


async def test_expired_is_gone_with_suggestions():
    repo = FakeRepo([
        S("old", expires_at=utcnow() - timedelta(seconds=1)),
        S("older-docs"),
    ])
    res = await resolve("old", repo)
    assert res.outcome == "gone"
    # v2 parity: ilike %old% matches the expired row itself; shortest first.
    assert [s.pattern for s in res.suggestions] == ["old", "older-docs"]


async def test_private_visibility_gates():
    repo = FakeRepo([S("secret", visibility=Visibility.PRIVATE, owner_email="a@b.c")])
    assert (await resolve("secret", repo)).outcome == "forbidden"
    assert (await resolve("secret", repo, is_admin=True)).outcome == "redirect"
    res = await resolve("secret", repo, current_user="a@b.c")
    assert res.outcome == "redirect"


async def test_unsafe_target_blocked():
    repo = FakeRepo([S("evil", "javascript:alert(1)")])
    assert (await resolve("evil", repo)).outcome == "unsafe"
    assert not is_safe_redirect_target("data:text/html,hi")
    assert is_safe_redirect_target("https://x.example")


async def test_not_found_suggestions_ranked():
    repo = FakeRepo([S("documentation-hub"), S("docs"), S("docker")])
    res = await resolve("doc", repo)
    assert res.outcome == "not_found"
    assert [s.pattern for s in res.suggestions] == ["docs", "docker", "documentation-hub"]


async def test_upstream_cache_hit_redirects_without_count():
    repo = FakeRepo([], cache=[UpstreamCache(pattern="bit", upstream_name="bitly",
                                             resolved_url="https://bit.ly/x")])
    res = await resolve("bit", repo)
    assert (res.outcome, res.target, res.source) == ("redirect", "https://bit.ly/x", "upstream")
    assert repo.increments == []


def test_placeholder_var_order():
    assert get_placeholder_vars("https://x/[user]/{repo}") == ["user", "repo"]
