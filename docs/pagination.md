# v3 pagination + filtering spec (EPIC-03 task 4)

Verified against `backend/modules/shortcuts/repository.py::list_paged` and
the routers below. Two tiers exist by design; cursor pagination is a
graduation trigger, not today's build.

## Tier 1 — full pagination: `GET /api/v1/shortcuts`

| Param | Default | Rules |
|---|---|---|
| `page` | `1` | `>= 1`; beyond the last page returns `data: []` (never 404) |
| `pageSize` | `20` | `1–100` |
| `q` | `""` | **Case-insensitive substring** over pattern **or** target (`%q%` both sides, lowercased). Prefix queries (`doc%`) are NOT special-cased — substring covers them |
| `tag` | `""` | Case-insensitive substring over the tags blob |
| `sort` | `updated_at` | `updated_at` (desc) · `created_at` (desc) · `popular` (`access_count` desc, `updated_at` desc tiebreak) |

Envelope: `{data: [...], meta: {page, pageSize, total}}` — `total` counts
the filtered set, so clients can render page counts without an extra call.

## Tier 2 — bounded lists (no pages)

Small, naturally-bounded collections return full arrays with a `limit` cap
where they can grow:

| Endpoint | Cap |
|---|---|
| `GET /api/v1/upstreams/check-logs` (`?upstream=`, newest first) | `limit` 1–200, default 50 |
| `GET /api/v1/jobs` (newest first) | `limit` 1–200, default 50 |
| `GET /api/v1/upstreams`, `/api/v1/upstreams/cache`, `/api/v1/admin/backup`, `/api/v1/auth/api-keys` | uncapped (admin-scale tables by construction) |

## Cursor graduation (open)

Past ~10k shortcuts, offset pages degrade (late offsets scan + discard).
The trigger to graduate `GET /api/v1/shortcuts` to cursor pagination
(`?cursor=` opaque, `next_cursor` in `meta`) is measured p95 list latency,
not row count — see the load-smoke p99s. Offset stays until then; the
envelope already carries everything a cursor variant needs.
