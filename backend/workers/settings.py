"""arq worker settings: `arq backend.workers.settings.WorkerSettings`."""

from typing import Any

from backend.core.config import settings
from backend.core.db import engine as db_engine
from backend.modules.jobs.redis import redis_settings_from_url
from backend.workers.backup import run_backup_create
from backend.workers.upstream import run_upstream_resync


async def on_shutdown(ctx: dict[str, Any]) -> None:
    """Drop pooled DB connections so the worker process exits cleanly.

    Pooled aiosqlite connections are pinned to their creating loop;
    leaving them pooled across loop teardown hangs interpreter exit
    (same class as the test-helper dispose in tests/test_v3_jobs_api.py).
    """
    await db_engine.dispose()


class WorkerSettings:
    functions = [run_upstream_resync, run_backup_create]
    redis_settings = redis_settings_from_url(settings.redis_url)
    # Resync-all over hundreds of patterns outlives arq's 5-minute default.
    job_timeout = 1800
    on_shutdown = on_shutdown
