"""Action policy: which mutations are public vs admin-only (EPIC-05 slice).

Reads and writes nothing by itself — the policy lives in the `settings`
table under `auth.public_actions` (a JSON list, curated via
`PATCH /api/v1/admin/config`, readable by anyone at
`GET /api/v1/site/policy`). Reads and admin writes stay public; every
other mutation requires an admin identity unless its action is listed.

Recognized actions (anything else in the stored list is ignored):

- `shortcuts.create` — POST /api/v1/shortcuts
- `upstreams.create` — POST /api/v1/upstreams

Update/delete paths stay admin-only unconditionally: a publicly created
private/team shortcut is still gated at resolve time by visibility.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.db import get_session
from backend.core.security import get_current_admin, security
from backend.models.entities import Setting

PUBLIC_ACTIONS_KEY = "auth.public_actions"

KNOWN_PUBLIC_ACTIONS = ("shortcuts.create", "upstreams.create")


async def get_public_actions(session: AsyncSession) -> set[str]:
    """Return the curated public-action set (unknown entries dropped)."""
    row = (
        await session.execute(select(Setting).where(Setting.key == PUBLIC_ACTIONS_KEY))
    ).scalar_one_or_none()
    if row is None or not isinstance(row.value, list):
        return set()
    return {str(item) for item in row.value if item in KNOWN_PUBLIC_ACTIONS}


class RequireAction:
    """Dependency factory: public action passes, anything else needs admin.

    Usage: `admin: Annotated[dict, Depends(RequireAction("shortcuts.create"))]`.
    The returned identity is `{"role": "public", ...}` for open actions and
    the admin identity otherwise (401/403 codes unchanged).
    """

    def __init__(self, action: str) -> None:
        self.action = action

    async def __call__(
        self,
        session: Annotated[AsyncSession, Depends(get_session)],
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    ) -> dict[str, Any]:
        public = await get_public_actions(session)
        if self.action in public:
            return {"role": "public", "scopes": [], "auth_method": "none"}
        return await get_current_admin(credentials, session)
