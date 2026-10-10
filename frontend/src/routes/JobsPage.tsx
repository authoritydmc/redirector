import { useCallback, useEffect, useState } from 'react'
import { ApiError } from '../lib/client'
import { cancelJob, listJobs, type Job } from '../features/admin/api'

const TERMINAL = new Set(['succeeded', 'failed', 'cancelled'])

export default function JobsPage() {
  const [rows, setRows] = useState<Job[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(async () => {
    try {
      setRows(await listJobs())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load jobs')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    setLoading(true)
    void reload()
    const timer = setInterval(reload, 5000)
    return () => clearInterval(timer)
  }, [reload])

  async function cancel(id: number) {
    setError(null)
    try {
      await cancelJob(id)
      await reload()
    } catch (err) {
      const message =
        err instanceof ApiError && err.status === 401
          ? 'Session expired — sign in again'
          : err instanceof Error
            ? err.message
            : 'Cancel failed'
      setError(message)
    }
  }

  return (
    <section>
      <div className="flex items-center gap-3">
        <h2 className="text-xl font-semibold">Jobs</h2>
        <button
          type="button"
          onClick={() => { setLoading(true); void reload() }}
          className="ml-auto rounded border border-rd-line px-3 py-1.5 text-sm"
        >
          Refresh
        </button>
      </div>
      {error !== null && <p role="alert" className="mt-3 text-sm text-rd-danger">{error}</p>}
      {loading && rows.length === 0 ? (
        <p className="mt-4 text-sm text-rd-muted">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="mt-4 text-sm text-rd-muted">No jobs yet. Backups and resyncs enqueue here.</p>
      ) : (
        <div className="mt-3 overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-rd-line">
              <th className="py-1 pr-2">ID</th>
              <th className="py-1 pr-2">Kind</th>
              <th className="py-1 pr-2">Status</th>
              <th className="py-1 pr-2">Progress</th>
              <th className="py-1">Actions</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id} className="border-b border-rd-line">
                <td className="py-1 pr-2 font-mono">{row.id}</td>
                <td className="py-1 pr-2 font-mono">{row.kind}</td>
                <td className="py-1 pr-2">{row.status}</td>
                <td className="py-1 pr-2">
                  {row.total > 0 ? `${row.done}/${row.total}` : '—'}
                  {row.error !== null && row.error !== undefined && row.error !== '' && (
                    <span className="block text-xs text-rd-danger">{row.error}</span>
                  )}
                </td>
                <td className="py-1">
                  {!TERMINAL.has(row.status) ? (
                    <button type="button" onClick={() => void cancel(row.id)} className="underline text-rd-muted">
                      Cancel
                    </button>
                  ) : (
                    <span className="text-rd-muted">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}
    </section>
  )
}
