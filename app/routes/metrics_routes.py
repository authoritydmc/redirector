"""Team KPI dashboard, realtime metrics page and their JSON APIs.

The home page (``/``) is a shortcut browser. Operators also need answers to
"how is the instance doing": total usage, what is popular, what is stale or
expired, upstream health, and whether the process itself is healthy right now.

* ``GET /metrics`` renders the KPI page (server-side, no JS required).
* ``GET /metrics/live`` renders the realtime page, which polls ``/api/metrics/live``.
* ``GET /api/kpi`` returns the same aggregates the KPI page renders, as JSON.
* ``GET /api/metrics/live`` returns a lightweight process snapshot for polling.

Everything here is derived from tables that already exist (``redirects``,
``upstream_cache``, ``upstream_check_log``, ``user_params``) plus process
stats, so no schema migration is needed. Every section is computed
defensively: a corrupt timestamp or a missing table degrades that card to a
zero/``null`` instead of 500ing the page.
"""

import logging
import os
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, render_template

from app.routes.routesUtils import login_required

bp = Blueprint('metrics', __name__)

logger = logging.getLogger(__name__)

CREATED_WINDOW_DAYS = 30


def _parse_dt(value):
    """Parse the string timestamps this app stores, or return ``None``.

    ``created_at``/``updated_at`` are written as ``YYYY-MM-DD HH:MM:SS`` while
    ``tried_at``/``expires_at`` are full ISO-8601. Naive values are assumed to
    be UTC, matching how they were written.
    """
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _day_key(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")


def _redirect_rows():
    from model.redirect import Redirect

    try:
        return Redirect.query.all()
    except Exception:
        logger.warning("KPI: could not read redirects table", exc_info=True)
        return []


def _overview(rows, now):
    total = len(rows)
    hits = [r.access_count or 0 for r in rows]
    total_hits = sum(hits)
    zero_hit = sum(1 for h in hits if h <= 0)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)
    created_7d = created_30d = 0
    for r in rows:
        created = _parse_dt(getattr(r, "created_at", None))
        if created is None:
            continue
        if created >= month_ago:
            created_30d += 1
            if created >= week_ago:
                created_7d += 1
    top = max(rows, key=lambda r: r.access_count or 0, default=None)
    return {
        "total_shortcuts": total,
        "total_hits": total_hits,
        "avg_hits": round(total_hits / total, 1) if total else 0,
        "zero_hit_count": zero_hit,
        "zero_hit_pct": round(100 * zero_hit / total, 1) if total else 0,
        "created_7d": created_7d,
        "created_30d": created_30d,
        "most_popular": (
            {"pattern": top.pattern, "target": top.target, "hits": top.access_count or 0}
            if top is not None
            else None
        ),
    }


def _breakdowns(rows):
    by_type, type_hits = Counter(), Counter()
    by_visibility = Counter()
    tag_counter = Counter()
    buckets = {"0": 0, "1-10": 0, "11-100": 0, "101+": 0}
    for r in rows:
        rtype = (getattr(r, "type", None) or "unknown").lower()
        by_type[rtype] += 1
        type_hits[rtype] += r.access_count or 0
        by_visibility[(getattr(r, "visibility", None) or "unknown").lower()] += 1
        for tag in (getattr(r, "tags", None) or "").split(","):
            tag = tag.strip().lower()
            if tag:
                tag_counter[tag] += 1
        hits = r.access_count or 0
        if hits <= 0:
            buckets["0"] += 1
        elif hits <= 10:
            buckets["1-10"] += 1
        elif hits <= 100:
            buckets["11-100"] += 1
        else:
            buckets["101+"] += 1
    return {
        "by_type": [
            {"type": t, "shortcuts": by_type[t], "hits": type_hits[t]}
            for t in sorted(by_type)
        ],
        "by_visibility": [
            {"visibility": v, "shortcuts": by_visibility[v]}
            for v in sorted(by_visibility)
        ],
        "top_tags": [
            {"tag": tag, "shortcuts": count}
            for tag, count in tag_counter.most_common(10)
        ],
        "hit_buckets": [
            {"bucket": b, "shortcuts": buckets[b]} for b in ("0", "1-10", "11-100", "101+")
        ],
    }


def _tables(rows, now):
    ranked = sorted(rows, key=lambda r: r.access_count or 0, reverse=True)
    top = [
        {
            "pattern": r.pattern,
            "target": r.target,
            "type": getattr(r, "type", None),
            "hits": r.access_count or 0,
            "updated_at": getattr(r, "updated_at", None),
        }
        for r in ranked[:10]
    ]
    dated = [r for r in rows if _parse_dt(getattr(r, "updated_at", None)) is not None]
    dated.sort(key=lambda r: _parse_dt(r.updated_at), reverse=True)
    recent = [
        {
            "pattern": r.pattern,
            "target": r.target,
            "hits": r.access_count or 0,
            "updated_at": r.updated_at,
        }
        for r in dated[:10]
    ]
    expired = expiring_7d = 0
    for r in rows:
        expiry = _parse_dt(getattr(r, "expires_at", None))
        if expiry is None:
            continue
        if expiry <= now:
            expired += 1
        elif expiry <= now + timedelta(days=7):
            expiring_7d += 1
    return {
        "top": top,
        "recent": recent,
        "expired_count": expired,
        "expiring_7d": expiring_7d,
    }


def _created_series(rows, now):
    days = [now - timedelta(days=i) for i in range(CREATED_WINDOW_DAYS - 1, -1, -1)]
    series = [{"day": _day_key(d), "created": 0} for d in days]
    by_day = {entry["day"]: entry for entry in series}
    for r in rows:
        created = _parse_dt(getattr(r, "created_at", None))
        if created is None:
            continue
        key = _day_key(created)
        if key in by_day:
            by_day[key]["created"] += 1
    # Hits are cumulative counters, not events, so there is no honest per-day
    # hits series without a hit log. The template labels the chart accordingly.
    return series


def _upstream():
    summary = {
        "cached_entries": 0,
        "logged_checks": 0,
        "total_checks": 0,
        "cached_checks": 0,
        "results": [],
        "recent": [],
    }
    try:
        from model.upstream_cache import UpstreamCache

        summary["cached_entries"] = UpstreamCache.query.count()
    except Exception:
        logger.warning("KPI: could not read upstream_cache", exc_info=True)
    try:
        from model.upstream_check_log import UpstreamCheckLog

        logs = UpstreamCheckLog.query.all()
        summary["logged_checks"] = len(logs)
        summary["total_checks"] = sum(l.count or 0 for l in logs)
        summary["cached_checks"] = sum(1 for l in logs if getattr(l, "cached", False))
        results = Counter((l.result or "unknown") for l in logs)
        summary["results"] = [
            {"result": result, "checks": results[result]} for result in sorted(results)
        ]
        dated = [l for l in logs if _parse_dt(getattr(l, "tried_at", None)) is not None]
        dated.sort(key=lambda l: _parse_dt(l.tried_at), reverse=True)
        summary["recent"] = [
            {
                "pattern": l.pattern,
                "upstream": l.upstream_name,
                "result": l.result,
                "count": l.count or 0,
                "cached": bool(getattr(l, "cached", False)),
                "tried_at": l.tried_at,
            }
            for l in dated[:10]
        ]
    except Exception:
        logger.warning("KPI: could not read upstream_check_log", exc_info=True)
    return summary


def _content():
    summary = {"user_params": 0}
    try:
        from model.user_param import UserParam

        summary["user_params"] = UserParam.query.count()
    except Exception:
        logger.warning("KPI: could not read user_params", exc_info=True)
    return summary


def _cache_stats():
    """Hit/miss stats for both cache layers.

    * Redis: ``keyspace_hits``/``keyspace_misses`` from ``INFO stats`` - the
      real hit rate of the shared cache. Only available when Redis is
      connected; otherwise the values stay ``None`` (unknown, not zero).
    * Upstream checks: each ``upstream_check_log`` row records whether that
      check was served from cache (``cached``) and how many times it ran
      (``count``), so hits/misses are exact with no extra bookkeeping.
    """
    stats = {
        "redis": {"connected": False, "hits": None, "misses": None, "hit_rate": None},
        "upstream": {"hits": 0, "misses": 0, "hit_rate": 0},
    }
    try:
        from app.config import config

        client = getattr(config, "redis_client", None)
        if config.redis_enabled and client is not None:
            try:
                info = client.info("stats")
                hits = int(info.get("keyspace_hits", 0))
                misses = int(info.get("keyspace_misses", 0))
                total = hits + misses
                stats["redis"] = {
                    "connected": True,
                    "hits": hits,
                    "misses": misses,
                    "hit_rate": round(100 * hits / total, 1) if total else 0,
                }
            except Exception:
                logger.warning("KPI: redis INFO unavailable", exc_info=True)
    except Exception:
        logger.warning("KPI: redis cache stats unavailable", exc_info=True)
    try:
        from model.upstream_check_log import UpstreamCheckLog

        hits = misses = 0
        for log in UpstreamCheckLog.query.all():
            if getattr(log, "cached", False):
                hits += log.count or 0
            else:
                misses += log.count or 0
        total = hits + misses
        stats["upstream"] = {
            "hits": hits,
            "misses": misses,
            "hit_rate": round(100 * hits / total, 1) if total else 0,
        }
    except Exception:
        logger.warning("KPI: upstream cache stats unavailable", exc_info=True)
    return stats


def _system():
    from app.CONSTANTS import get_semver
    from app.config import config
    from app.routes.version_routes import get_system_info

    info = {"version": get_semver(), "in_docker": config.RUNNING_IN_DOCKER}
    try:
        info.update(get_system_info())
    except Exception:
        logger.warning("KPI: system info unavailable", exc_info=True)
    try:
        from app.utils import state as state_mod
        from app.utils.utils import get_db_uri

        status = state_mod.schema_status(get_db_uri())
        info["schema_current"] = status.get("current")
        info["schema_head"] = status.get("head")
        info["schema_drift"] = status.get("drift")
        info["schema_pending"] = status.get("pending", [])
    except Exception:
        logger.warning("KPI: schema status unavailable", exc_info=True)
        info["schema_current"] = info["schema_head"] = None
        info["schema_drift"] = "unknown"
        info["schema_pending"] = []
    try:
        db_file = os.path.join(config.DATA_DIR, "redirect.db")
        info["db_size_kb"] = round(os.path.getsize(db_file) / 1024, 1)
    except OSError:
        info["db_size_kb"] = None
    info["redis_enabled"] = bool(config.redis_enabled)
    return info


def collect_kpi():
    """Full KPI snapshot for ``/metrics`` and ``/api/kpi``."""
    now = datetime.now(timezone.utc)
    rows = _redirect_rows()
    return {
        "ok": True,
        "generated_at": now.isoformat(),
        "overview": _overview(rows, now),
        "breakdowns": _breakdowns(rows),
        "tables": _tables(rows, now),
        "created_series": _created_series(rows, now),
        "upstream": _upstream(),
        "cache": _cache_stats(),
        "content": _content(),
        "system": _system(),
    }


def _timed(ping):
    start = time.perf_counter()
    try:
        ok = ping()
    except Exception:
        return False, None
    latency_ms = round((time.perf_counter() - start) * 1000, 1)
    return bool(ok), latency_ms


def collect_live():
    """Small snapshot for 5s polling. Must stay cheap: counts, not full rows."""
    from sqlalchemy import func

    from app.CONSTANTS import get_semver
    from app.config import config
    from app.routes.version_routes import get_system_info
    from model import db
    from model.redirect import Redirect

    snapshot = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version": get_semver(),
        # get_system_info() only includes these when psutil is installed;
        # keep the keys stable so pollers never have to guess.
        "uptime_seconds": None,
        "memory_mb": None,
        "cpu_percent": None,
    }
    snapshot.update(get_system_info())
    try:
        db_ok, db_ms = _timed(lambda: db.session.execute(db.text("SELECT 1")) is not None)
    except Exception:
        db_ok, db_ms = False, None
    snapshot["db_up"] = db_ok
    snapshot["db_latency_ms"] = db_ms
    snapshot["redis_enabled"] = bool(config.redis_enabled)
    if config.redis_enabled and config.redis_client:
        redis_up, redis_ms = _timed(config.redis_client.ping)
    else:
        redis_up, redis_ms = None, None
    snapshot["redis_up"] = redis_up
    snapshot["redis_latency_ms"] = redis_ms
    try:
        snapshot["total_shortcuts"] = Redirect.query.count()
        snapshot["total_hits"] = (
            db.session.query(func.coalesce(func.sum(Redirect.access_count), 0)).scalar() or 0
        )
    except Exception:
        logger.warning("live snapshot: shortcut totals unavailable", exc_info=True)
        snapshot["total_shortcuts"] = 0
        snapshot["total_hits"] = 0
    try:
        top = (
            Redirect.query.order_by(Redirect.access_count.desc()).limit(5).all()
        )
        snapshot["top"] = [
            {"pattern": r.pattern, "hits": r.access_count or 0} for r in top
        ]
        recent = Redirect.query.order_by(Redirect.updated_at.desc()).limit(5).all()
        snapshot["recent"] = [
            {
                "pattern": r.pattern,
                "hits": r.access_count or 0,
                "updated_at": r.updated_at,
            }
            for r in recent
        ]
    except Exception:
        logger.warning("live snapshot: top/recent unavailable", exc_info=True)
        snapshot["top"] = []
        snapshot["recent"] = []
    try:
        from model.upstream_cache import UpstreamCache

        snapshot["upstream_cached"] = UpstreamCache.query.count()
    except Exception:
        snapshot["upstream_cached"] = 0
    cache = _cache_stats()
    snapshot["redis_cache"] = cache["redis"]
    snapshot["upstream_cache_stats"] = cache["upstream"]
    try:
        from app.utils import state as state_mod
        from app.utils.utils import get_db_uri

        snapshot["schema_drift"] = state_mod.schema_status(get_db_uri()).get("drift")
    except Exception:
        snapshot["schema_drift"] = "unknown"
    return snapshot


@bp.route('/metrics')
@login_required
def metrics_page():
    """KPI dashboard: usage, content mix, upstream health, system."""
    from app.CONSTANTS import get_semver

    try:
        kpi = collect_kpi()
    except Exception:  # never let a diagnostics page 500
        logger.exception("KPI collection failed")
        kpi = None
    return render_template('metrics.html', version=get_semver(), kpi=kpi)


@bp.route('/metrics/live')
@login_required
def metrics_live_page():
    """Realtime page. Data arrives via ``/api/metrics/live`` polling."""
    from app.CONSTANTS import get_semver

    return render_template('metrics_live.html', version=get_semver())


@bp.route('/api/kpi')
@login_required
def api_kpi():
    """Machine-readable KPI snapshot (same payload the page renders)."""
    try:
        return jsonify(collect_kpi())
    except Exception:
        logger.exception("KPI API failed")
        return jsonify({"ok": False, "error": "KPI collection failed"}), 500


@bp.route('/api/metrics/live')
@login_required
def api_metrics_live():
    """Lightweight snapshot for realtime polling (every few seconds)."""
    try:
        return jsonify(collect_live())
    except Exception:
        logger.exception("live metrics API failed")
        return jsonify({"ok": False, "error": "live snapshot failed"}), 500
