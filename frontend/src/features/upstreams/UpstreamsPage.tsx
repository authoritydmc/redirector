import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { createUpstream, deleteUpstream, listUpstreams, updateUpstream } from './api'
import type { CheckEvent, Upstream } from './api'
import { useAuth } from '../../lib/auth'
import { getSitePolicy } from '../../lib/policy'
import {
  clearCheckLogs,
  listCache,
  listCheckLogs,
  purgeCache,
  purgeCacheEntry,
  resyncCache,
  type CacheEntry,
  type CheckLog,
} from '../admin/api'

export default function UpstreamsPage() {
  const { token } = useAuth()
  const [rows, setRows] = useState<Upstream[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [confirming, setConfirming] = useState<number | null>(null)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editName, setEditName] = useState('')
  const [editBaseUrl, setEditBaseUrl] = useState('')
  const [cacheRows, setCacheRows] = useState<CacheEntry[]>([])
  const [cacheFilter, setCacheFilter] = useState('')
  const [confirmPurge, setConfirmPurge] = useState(false)
  const [resyncUpstream, setResyncUpstream] = useState('')
  const [resyncPattern, setResyncPattern] = useState('')
  const [cacheNotice, setCacheNotice] = useState<string | null>(null)
  const [logRows, setLogRows] = useState<CheckLog[]>([])

  const [probe, setProbe] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [events, setEvents] = useState<CheckEvent[]>([])
  const sourceRef = useRef<EventSource | null>(null)
  const [publicCreate, setPublicCreate] = useState(false)

  useEffect(() => {
    if (token !== null) {
      return
    }
    getSitePolicy().then(
      (policy) => setPublicCreate((policy.public_actions ?? []).includes('upstreams.create')),
      () => setPublicCreate(false),
    )
  }, [token])

  const canCreate = token !== null || publicCreate
  const canManage = token !== null

  const reload = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [ups, cache, logs] = await Promise.all([
        listUpstreams(),
        listCache().catch(() => [] as CacheEntry[]),
        listCheckLogs().catch(() => [] as CheckLog[]),
      ])
      setRows(ups)
      setCacheRows(cache)
      setLogRows(logs)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load upstreams')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void reload()
    return () => {
      sourceRef.current?.close()
      sourceRef.current = null
    }
  }, [reload])

  async function create(event: FormEvent) {
    event.preventDefault()
    setError(null)
    try {
      await createUpstream({ name: name.trim(), base_url: baseUrl.trim() })
      setName('')
      setBaseUrl('')
      await reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Create failed')
    }
  }

  async function remove(id: number) {
    try {
      await deleteUpstream(id)
      setConfirming(null)
      await reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Delete failed')
    }
  }

  function startEdit(row: Upstream) {
    if (row.id === null || row.id === undefined) {
      return
    }
    setEditingId(row.id)
    setEditName(row.name)
    setEditBaseUrl(row.base_url)
  }

  async function saveEdit(id: number) {
    setError(null)
    try {
      await updateUpstream(id, { name: editName.trim(), base_url: editBaseUrl.trim() })
      setEditingId(null)
      await reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Update failed')
    }
  }

  async function refreshCache() {
    setCacheNotice(null)
    try {
      setCacheRows(await listCache(cacheFilter.trim() === '' ? undefined : cacheFilter.trim()))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Cache load failed')
    }
  }

  async function purgeAll() {
    setCacheNotice(null)
    try {
      const res = await purgeCache(cacheFilter.trim() === '' ? undefined : cacheFilter.trim())
      setCacheNotice(`Purged ${res.purged} entr${res.purged === 1 ? 'y' : 'ies'}.`)
      setConfirmPurge(false)
      await refreshCache()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Purge failed')
    }
  }

  async function purgeOne(upstream: string, pattern: string) {
    setCacheNotice(null)
    try {
      await purgeCacheEntry(upstream, pattern)
      await refreshCache()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Entry purge failed')
    }
  }

  async function resync(event: FormEvent) {
    event.preventDefault()
    if (resyncUpstream.trim() === '') {
      return
    }
    setCacheNotice(null)
    try {
      const res = await resyncCache(
        resyncUpstream.trim(),
        resyncPattern.trim() === '' ? undefined : resyncPattern.trim(),
      )
      setCacheNotice(`Checked ${res.checked}, updated ${res.updated}.`)
      await refreshCache()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Resync failed')
    }
  }

  async function clearLogs() {
    setCacheNotice(null)
    try {
      await clearCheckLogs()
      setLogRows(await listCheckLogs().catch(() => [] as CheckLog[]))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Clear logs failed')
    }
  }

  function startProbe() {
    const pattern = probe.trim().replace(/^\/+|\/+$/g, '')
    if (pattern === '' || streaming) {
      return
    }
    setEvents([])
    setError(null)
    setStreaming(true)
    const source = new EventSource(
      `/api/v1/upstreams/check/stream/${encodeURIComponent(pattern)}`,
    )
    sourceRef.current = source
    source.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data) as CheckEvent
        setEvents((prev) => [...prev, payload])
        if (payload.done === true) {
          stopProbe()
        }
      } catch {
        // Non-JSON frames (e.g. proxies) are ignored.
      }
    }
    source.onerror = () => {
      setError('Check stream disconnected');
      stopProbe()
    }
  }

  function stopProbe() {
    sourceRef.current?.close()
    sourceRef.current = null
    setStreaming(false)
  }

  return (
    <section>
      <h2 className="text-xl font-semibold">Upstreams</h2>

      {canCreate && (
      <form onSubmit={create} className="mt-3 flex flex-wrap items-end gap-2">
        <label className="flex flex-col text-sm">
          Name
          <input
            aria-label="Upstream name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            className="mt-1 rounded border border-rd-line bg-rd-input px-3 py-1.5 text-sm text-rd-text"
          />
        </label>
        <label className="flex flex-col text-sm">
          Base URL
          <input
            aria-label="Base URL"
            value={baseUrl}
            onChange={(event) => setBaseUrl(event.target.value)}
            placeholder="https://go.example"
            className="mt-1 rounded border border-rd-line bg-rd-input px-3 py-1.5 text-sm text-rd-text"
          />
        </label>
        <button type="submit" className="rounded bg-rd-accent px-3 py-1.5 text-sm text-rd-accent-ink">
          Add upstream
        </button>
      </form>
      )}

      {error !== null && <p role="alert" className="mt-3 text-sm text-rd-danger">{error}</p>}

      {loading ? (
        <p className="mt-4 text-sm text-rd-muted">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="mt-4 text-sm text-rd-muted">No upstreams configured.</p>
      ) : (
        <div className="mt-3 overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-rd-line">
              <th className="py-1 pr-2">Name</th>
              <th className="py-1 pr-2">Base URL</th>
              {canManage && <th className="py-1">Actions</th>}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id ?? row.name} className="border-b border-rd-line">
                <td className="py-1 pr-2 font-mono">{row.name}</td>
                <td className="max-w-xs truncate py-1 pr-2">{row.base_url}</td>
                {canManage && (
                <td className="py-1">
                  {editingId !== null && editingId === row.id ? (
                    <span className="flex flex-wrap items-center gap-2">
                      <input
                        aria-label="Edit name"
                        value={editName}
                        onChange={(event) => setEditName(event.target.value)}
                        className="rounded border border-rd-line bg-rd-input px-2 py-1 font-mono text-xs text-rd-text"
                      />
                      <input
                        aria-label="Edit base URL"
                        value={editBaseUrl}
                        onChange={(event) => setEditBaseUrl(event.target.value)}
                        className="rounded border border-rd-line bg-rd-input px-2 py-1 font-mono text-xs text-rd-text"
                      />
                      <button type="button" onClick={() => void saveEdit(row.id as number)} className="underline text-rd-muted">
                        Save
                      </button>
                      <button type="button" onClick={() => setEditingId(null)} className="underline text-rd-muted">
                        Cancel
                      </button>
                    </span>
                  ) : row.id !== null && row.id !== undefined && confirming === row.id ? (
                    <span className="flex gap-2">
                      <button type="button" onClick={() => void remove(row.id as number)} className="text-rd-danger underline">
                        Confirm
                      </button>
                      <button type="button" onClick={() => setConfirming(null)} className="underline text-rd-muted">
                        Cancel
                      </button>
                    </span>
                  ) : (
                    <span className="flex gap-2">
                      <button
                        type="button"
                        disabled={row.id === null || row.id === undefined}
                        onClick={() => startEdit(row)}
                        className="underline text-rd-muted disabled:opacity-40"
                      >
                        Edit
                      </button>
                    <button
                      type="button"
                      disabled={row.id === null || row.id === undefined}
                      onClick={() => setConfirming(row.id as number)}
                      className="underline text-rd-muted disabled:opacity-40"
                    >
                      Delete
                    </button>
                    </span>
                  )}
                </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}

      <h3 className="mt-6 text-lg font-semibold">Live check</h3>
      <div className="mt-2 flex flex-wrap items-end gap-2">
        <label className="flex flex-col text-sm">
          Pattern to check
          <input
            aria-label="Pattern to check"
            value={probe}
            onChange={(event) => setProbe(event.target.value)}
            className="mt-1 rounded border border-rd-line bg-rd-input px-3 py-1.5 font-mono text-sm text-rd-text"
          />
        </label>
        {streaming ? (
          <button type="button" onClick={stopProbe} className="rounded border border-rd-line px-3 py-1.5 text-sm">
            Stop
          </button>
        ) : (
          <button
            type="button"
            onClick={startProbe}
            disabled={probe.trim() === ''}
            className="rounded bg-rd-accent px-3 py-1.5 text-sm text-rd-accent-ink disabled:opacity-50"
          >
            Check
          </button>
        )}
      </div>
      <ul aria-label="Check events" className="mt-3 flex flex-col gap-1 text-sm">
        {events.map((event, index) => (
          <li key={index} className="rounded border border-rd-line px-2 py-1 font-mono text-xs">
            {event.done === true
              ? 'done'
              : event.message ?? `${event.upstream_name ?? '?'}: ${event.status ?? '…'}`}
          </li>
        ))}
      </ul>

      <h3 className="mt-6 text-lg font-semibold">Shortcut cache</h3>
      {cacheNotice !== null && <p role="status" className="mt-2 text-sm text-rd-muted">{cacheNotice}</p>}
      {canManage && (
      <form onSubmit={resync} className="mt-2 flex flex-wrap items-end gap-2">
        <label className="flex flex-col text-sm">
          Upstream
          <input
            aria-label="Resync upstream"
            value={resyncUpstream}
            onChange={(event) => setResyncUpstream(event.target.value)}
            placeholder="name, empty = all cached"
            className="mt-1 rounded border border-rd-line bg-rd-input px-3 py-1.5 text-sm text-rd-text"
          />
        </label>
        <label className="flex flex-col text-sm">
          Pattern (optional)
          <input
            aria-label="Resync pattern"
            value={resyncPattern}
            onChange={(event) => setResyncPattern(event.target.value)}
            className="mt-1 rounded border border-rd-line bg-rd-input px-3 py-1.5 font-mono text-sm text-rd-text"
          />
        </label>
        <button type="submit" className="rounded bg-rd-accent px-3 py-1.5 text-sm text-rd-accent-ink">
          Resync
        </button>
      </form>
      )}
      <div className="mt-2 flex flex-wrap items-end gap-2">
        <label className="flex flex-col text-sm">
          Filter by upstream
          <input
            aria-label="Cache filter"
            value={cacheFilter}
            onChange={(event) => setCacheFilter(event.target.value)}
            className="mt-1 rounded border border-rd-line bg-rd-input px-3 py-1.5 text-sm text-rd-text"
          />
        </label>
        <button type="button" onClick={() => void refreshCache()} className="rounded border border-rd-line px-3 py-1.5 text-sm">
          Load cache
        </button>
        {canManage && confirmPurge && (
          <span className="flex gap-2">
            <button type="button" onClick={() => void purgeAll()} className="rounded bg-rd-danger px-3 py-1.5 text-sm text-rd-danger-ink">
              Confirm purge
            </button>
            <button type="button" onClick={() => setConfirmPurge(false)} className="rounded border border-rd-line px-3 py-1.5 text-sm">
              Cancel
            </button>
          </span>
        )}
        {canManage && !confirmPurge && (
          <button type="button" onClick={() => setConfirmPurge(true)} className="rounded border border-rd-danger px-3 py-1.5 text-sm text-rd-danger">
            Purge cache
          </button>
        )}
      </div>
      {cacheRows.length > 0 && (
        <div className="mt-3 overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-rd-line">
              <th className="py-1 pr-2">Pattern</th>
              <th className="py-1 pr-2">Upstream</th>
              <th className="py-1 pr-2">Resolved URL</th>
              {canManage && <th className="py-1">Actions</th>}
            </tr>
          </thead>
          <tbody>
            {cacheRows.map((row) => (
              <tr key={`${row.upstream_name}:${row.pattern}`} className="border-b border-rd-line">
                <td className="py-1 pr-2 font-mono">{row.pattern}</td>
                <td className="py-1 pr-2 font-mono">{row.upstream_name}</td>
                <td className="max-w-xs truncate py-1 pr-2">{row.resolved_url ?? '—'}</td>
                {canManage && (
                <td className="py-1">
                  <button type="button" onClick={() => void purgeOne(row.upstream_name, row.pattern)} className="underline text-rd-muted">
                    Purge
                  </button>
                </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}

      <h3 className="mt-6 text-lg font-semibold">Check logs</h3>
      {canManage && (
      <div className="mt-2 flex gap-2">
        <button type="button" onClick={() => void clearLogs()} className="rounded border border-rd-line px-3 py-1.5 text-sm">
          Clear logs
        </button>
      </div>
      )}
      {logRows.length > 0 && (
        <ul aria-label="Check logs" className="mt-3 flex flex-col gap-1 text-sm">
          {logRows.slice(0, 50).map((row, index) => (
            <li key={row.id ?? index} className="rounded border border-rd-line px-2 py-1 font-mono text-xs">
              {row.pattern} @ {row.upstream_name}: {row.result ?? '?'} (×{row.count}) — {row.tried_at}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
