"""Asynchronous Upstream Check Service using httpx.AsyncClient (replaces blocking requests)."""

from __future__ import annotations

import httpx

from backend.models.entities import Upstream
from backend.modules.shortcuts.repository import is_sso_url
from backend.modules.upstreams.repository import UpstreamRepository
from backend.modules.upstreams.schemas import UpstreamCheckResult


class UpstreamCheckService:
    def __init__(self, repo: UpstreamRepository, client: httpx.AsyncClient | None = None) -> None:
        self.repo = repo
        self._client = client

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
                await self.repo.log_check(
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
                await self.repo.log_check(
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
                await self.repo.log_check(
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
            await self.repo.log_check(
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
