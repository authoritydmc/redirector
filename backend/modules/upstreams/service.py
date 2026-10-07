"""Asynchronous Upstream Check Service using httpx.AsyncClient (replaces blocking requests)."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from backend.models.entities import Upstream
from backend.modules.shortcuts.repository import is_sso_url
from backend.modules.upstreams.repository import UpstreamRepository
from backend.modules.upstreams.schemas import CacheResyncResult, UpstreamCheckResult


class UpstreamCheckService:
    def __init__(self, repo: UpstreamRepository) -> None:
        self.repo = repo
        # AsyncSession is not safe for concurrent use: fan-out keeps HTTP
        # parallel while repo writes serialize on this lock.
        self._lock = asyncio.Lock()

    async def _log_check(self, **kwargs: Any) -> None:
        async with self._lock:
            await self.repo.log_check(**kwargs)

    async def _save_cache(self, pattern: str, upstream_name: str, resolved_url: str) -> None:
        async with self._lock:
            await self.repo.save_cache(pattern, upstream_name, resolved_url)

    async def _clear_entry(self, pattern: str, upstream_name: str) -> bool:
        async with self._lock:
            return await self.repo.clear_cache_entry(pattern, upstream_name)

    async def check_single(
        self,
        upstream: Upstream,
        pattern: str,
        client: httpx.AsyncClient,
    ) -> UpstreamCheckResult:
        base = upstream.base_url.rstrip("/")
        if not base:
            return UpstreamCheckResult(
                upstream_name=upstream.name,
                check_url="",
                status="skipped",
                message="No base_url configured",
            )

        check_url = f"{base}/{pattern.strip().strip('/')}"

        # httpx takes `verify` at the client level, not per request: use a
        # dedicated insecure client for upstreams that opt out of TLS verify.
        try:
            if upstream.verify_ssl:
                resp = await client.get(check_url, follow_redirects=True, timeout=3.0)
            else:
                async with httpx.AsyncClient(verify=False, timeout=3.0) as insecure_client:
                    resp = await insecure_client.get(check_url, follow_redirects=True)
            actual_url = str(resp.url)
            status_code = resp.status_code

            # Check SSO
            if is_sso_url(actual_url):
                await self._log_check(
                    pattern=pattern,
                    upstream_name=upstream.name,
                    check_url=check_url,
                    result="sso_required",
                    detail=f"actual_url={actual_url}, status_code={status_code}",
                    cached=False,
                )
                return UpstreamCheckResult(
                    upstream_name=upstream.name,
                    check_url=check_url,
                    status="sso_required",
                    target_url=check_url,  # Direct to check_url so browser handles login
                    status_code=status_code,
                    cached=False,
                    message=f"SSO login required at {actual_url}",
                )

            # Check fail URL / status
            fail_url_match = actual_url.startswith(upstream.fail_url) if upstream.fail_url else False
            fail_status_match = (
                upstream.fail_status_code is not None and status_code == upstream.fail_status_code
            )

            if not fail_url_match and not fail_status_match:
                # Found!
                await self._log_check(
                    pattern=pattern,
                    upstream_name=upstream.name,
                    check_url=check_url,
                    result="success",
                    detail=f"actual_url={actual_url}, status_code={status_code}",
                    cached=True,
                )
                return UpstreamCheckResult(
                    upstream_name=upstream.name,
                    check_url=check_url,
                    status="found",
                    target_url=actual_url,
                    status_code=status_code,
                    cached=True,
                    message=f"Found target: {actual_url}",
                )
            else:
                await self._log_check(
                    pattern=pattern,
                    upstream_name=upstream.name,
                    check_url=check_url,
                    result="not_found",
                    detail=f"actual_url={actual_url}, status_code={status_code}",
                    cached=False,
                )
                return UpstreamCheckResult(
                    upstream_name=upstream.name,
                    check_url=check_url,
                    status="not_found",
                    status_code=status_code,
                    message="Shortcut not found in upstream",
                )

        except Exception as e:
            await self._log_check(
                pattern=pattern,
                upstream_name=upstream.name,
                check_url=check_url,
                result="error",
                detail=str(e),
                cached=False,
            )
            return UpstreamCheckResult(
                upstream_name=upstream.name,
                check_url=check_url,
                status="error",
                message=f"Connection error: {e}",
            )

    async def check_all(
        self,
        upstreams: list[Upstream],
        pattern: str,
        client: httpx.AsyncClient,
    ) -> list[UpstreamCheckResult]:
        """Concurrent fan-out (EPIC-06): check latency = max(upstreams), not
        sum. Results keep configured order so first-found/first-SSO priority
        is unchanged. Note: unlike the old sequential loop, every upstream is
        probed (check-log rows upsert per pattern+upstream, no row growth)."""
        if not upstreams:
            return []
        results = await asyncio.gather(
            *(self.check_single(up, pattern, client) for up in upstreams)
        )
        return list(results)

    async def refresh_patterns(
        self,
        upstream: Upstream,
        patterns: list[str],
        client: httpx.AsyncClient,
        max_concurrency: int = 10,
    ) -> tuple[list[CacheResyncResult], int, int]:
        """Re-check patterns and reconcile the cache (v2 resync port).

        found -> upsert cache row; sso/not_found/error -> drop the row
        (stale entries must not survive); skipped -> row left untouched.
        Returns (per-pattern results, updated count, cleared count).

        Phase 1 fans the HTTP checks out concurrently (bounded by
        `max_concurrency`); phase 2 reconciles cache rows sequentially so
        counts and result order stay deterministic. Session writes stay
        safe via the repo lock held in check_single/_save_cache/_clear_entry.
        """
        if not patterns:
            return [], 0, 0
        sem = asyncio.Semaphore(max(1, max_concurrency))

        async def _checked(pattern: str) -> UpstreamCheckResult:
            async with sem:
                return await self.check_single(upstream, pattern, client)

        checks = await asyncio.gather(*(_checked(p) for p in patterns))
        results: list[CacheResyncResult] = []
        updated = 0
        cleared = 0
        for pattern, check in zip(patterns, checks, strict=True):
            if check.status == "found" and check.target_url:
                await self._save_cache(pattern, upstream.name, check.target_url)
                updated += 1
                results.append(CacheResyncResult(
                    pattern=pattern, success=True, status="found",
                    resolved_url=check.target_url, message=check.message,
                ))
            elif check.status == "skipped":
                results.append(CacheResyncResult(
                    pattern=pattern, success=False, status="skipped",
                    message=check.message,
                ))
            else:
                if await self._clear_entry(pattern, upstream.name):
                    cleared += 1
                results.append(CacheResyncResult(
                    pattern=pattern, success=False, status=check.status,
                    message=check.message,
                ))
        return results, updated, cleared
