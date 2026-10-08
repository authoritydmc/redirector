import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { createUpstream, deleteUpstream, listUpstreams } from './api'
import type { CheckEvent, Upstream } from './api'

export default function UpstreamsPage() {
  const [rows, setRows] = useState<Upstream[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [confirming, setConfirming] = useState<number | null>(null)

  const [probe, setProbe] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [events, setEvents] = useState<CheckEvent[]>([])
  const sourceRef = useRef<EventSource | null>(null)

  const reload = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setRows(await listUpstreams())
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

      <form onSubmit={create} className="mt-3 flex flex-wrap items-end gap-2">
        <label className="flex flex-col text-sm">
          Name
          <input
            aria-label="Upstream name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            className="mt-1 rounded border px-3 py-1.5 text-sm dark:bg-white/10"
          />
        </label>
        <label className="flex flex-col text-sm">
          Base URL
          <input
            aria-label="Base URL"
            value={baseUrl}
            onChange={(event) => setBaseUrl(event.target.value)}
            placeholder="https://go.example"
            className="mt-1 rounded border px-3 py-1.5 text-sm dark:bg-white/10"
          />
        </label>
        <button type="submit" className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white">
          Add upstream
        </button>
      </form>

      {error !== null && <p role="alert" className="mt-3 text-sm text-red-600">{error}</p>}

      {loading ? (
        <p className="mt-4 text-sm opacity-70">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="mt-4 text-sm opacity-70">No upstreams configured.</p>
      ) : (
        <table className="mt-3 w-full text-left text-sm">
          <thead>
            <tr className="border-b dark:border-white/10">
              <th className="py-1 pr-2">Name</th>
              <th className="py-1 pr-2">Base URL</th>
              <th className="py-1">Actions</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id ?? row.name} className="border-b dark:border-white/10">
                <td className="py-1 pr-2 font-mono">{row.name}</td>
                <td className="max-w-xs truncate py-1 pr-2 opacity-80">{row.base_url}</td>
                <td className="py-1">
                  {row.id !== null && row.id !== undefined && confirming === row.id ? (
                    <span className="flex gap-2">
                      <button type="button" onClick={() => void remove(row.id as number)} className="text-red-600 underline">
                        Confirm
                      </button>
                      <button type="button" onClick={() => setConfirming(null)} className="underline opacity-70">
                        Cancel
                      </button>
                    </span>
                  ) : (
                    <button
                      type="button"
                      disabled={row.id === null || row.id === undefined}
                      onClick={() => setConfirming(row.id as number)}
                      className="underline opacity-70 disabled:opacity-40"
                    >
                      Delete
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h3 className="mt-6 text-lg font-semibold">Live check</h3>
      <div className="mt-2 flex flex-wrap items-end gap-2">
        <label className="flex flex-col text-sm">
          Pattern to check
          <input
            aria-label="Pattern to check"
            value={probe}
            onChange={(event) => setProbe(event.target.value)}
            className="mt-1 rounded border px-3 py-1.5 text-sm font-mono dark:bg-white/10"
          />
        </label>
        {streaming ? (
          <button type="button" onClick={stopProbe} className="rounded border px-3 py-1.5 text-sm">
            Stop
          </button>
        ) : (
          <button
            type="button"
            onClick={startProbe}
            disabled={probe.trim() === ''}
            className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white disabled:opacity-50"
          >
            Check
          </button>
        )}
      </div>
      <ul aria-label="Check events" className="mt-3 flex flex-col gap-1 text-sm">
        {events.map((event, index) => (
          <li key={index} className="rounded border px-2 py-1 font-mono text-xs dark:border-white/10">
            {event.done === true
              ? 'done'
              : event.message ?? `${event.upstream_name ?? '?'}: ${event.status ?? '…'}`}
          </li>
        ))}
      </ul>
    </section>
  )
}
