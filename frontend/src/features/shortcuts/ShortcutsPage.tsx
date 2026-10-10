import { useCallback, useEffect, useState } from 'react'
import { ApiError } from '../../lib/client'
import { bulkDeleteShortcuts, bulkImportShortcuts, createShortcut, deleteShortcut, listShortcuts, updateShortcut } from './api'
import type { Shortcut, ShortcutInput } from './api'
import ShortcutForm from './ShortcutForm'

const PAGE_SIZE = 20

function useDebounced(value: string, delayMs: number): string {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs)
    return () => clearTimeout(timer)
  }, [value, delayMs])
  return debounced
}

export default function ShortcutsPage() {
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState('updated_at')
  const [page, setPage] = useState(1)
  const [rows, setRows] = useState<Shortcut[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [confirming, setConfirming] = useState<string | null>(null)
  const [confirmingBulk, setConfirmingBulk] = useState(false)
  const [importNotice, setImportNotice] = useState<string | null>(null)
  const [drawer, setDrawer] = useState<{ mode: 'create' } | { mode: 'edit'; row: Shortcut } | null>(null)
  const [drawerError, setDrawerError] = useState<string | null>(null)

  const debouncedQuery = useDebounced(query, 300)

  const reload = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const list = await listShortcuts({ page, pageSize: PAGE_SIZE, q: debouncedQuery, sort })
      setRows(list.data)
      setTotal(list.meta.total)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load shortcuts')
    } finally {
      setLoading(false)
    }
  }, [page, debouncedQuery, sort])

  useEffect(() => {
    void reload()
  }, [reload])

  useEffect(() => {
    if (drawer === null) {
      return
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setDrawer(null)
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [drawer])

  function onSearch(value: string) {
    setQuery(value)
    setPage(1)
  }

  function onSort(value: string) {
    setSort(value)
    setPage(1)
  }

  function toggle(pattern: string) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(pattern)) {
        next.delete(pattern)
      } else {
        next.add(pattern)
      }
      return next
    })
  }

  function togglePage() {
    setSelected((prev) => {
      const next = new Set(prev)
      const allSelected = rows.every((row) => next.has(row.pattern))
      for (const row of rows) {
        if (allSelected) {
          next.delete(row.pattern)
        } else {
          next.add(row.pattern)
        }
      }
      return next
    })
  }

  async function removeOne(pattern: string) {
    try {
      await deleteShortcut(pattern)
      setConfirming(null)
      setSelected((prev) => {
        const next = new Set(prev)
        next.delete(pattern)
        return next
      })
      await reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Delete failed')
    }
  }

  async function removeSelected() {
    try {
      const result = await bulkDeleteShortcuts([...selected])
      if (result.not_found.length > 0) {
        setError(`Already gone: ${result.not_found.join(', ')}`)
      }
      setSelected(new Set())
      setConfirmingBulk(false)
      await reload()
    } catch (err) {
      const message = err instanceof ApiError && err.status === 401 ? 'Session expired — sign in again' : err instanceof Error ? err.message : 'Bulk delete failed'
      setError(message)
    }
  }

  async function saveDrawer(input: ShortcutInput) {    try {
      if (drawer?.mode === 'edit') {
        const { pattern: _ignored, ...patch } = input
        await updateShortcut(drawer.row.pattern, patch)
      } else {
        await createShortcut(input)
      }
      setDrawer(null)
      setDrawerError(null)
      await reload()
    } catch (err) {
      setDrawerError(err instanceof Error ? err.message : 'Save failed')
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const pageSelected = rows.length > 0 && rows.every((row) => selected.has(row.pattern))

  async function exportAll() {
    setError(null)
    try {
      const all: Shortcut[] = []
      let page = 1
      for (;;) {
        const list = await listShortcuts({ page, pageSize: 100, q: '', sort: 'updated_at' })
        all.push(...list.data)
        if (all.length >= list.meta.total || list.data.length === 0) {
          break
        }
        page += 1
      }
      const blob = new Blob([JSON.stringify({ shortcuts: all }, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = 'redirector-export.json'
      anchor.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Export failed')
    }
  }

  async function importFile(file: File) {
    setError(null)
    setImportNotice(null)
    try {
      const parsed: unknown = JSON.parse(await file.text())
      const list = Array.isArray(parsed)
        ? parsed
        : Array.isArray((parsed as { shortcuts?: unknown }).shortcuts)
          ? (parsed as { shortcuts: ShortcutInput[] }).shortcuts
          : null
      if (list === null) {
        throw new Error('Expected {"shortcuts": [...]} or a bare array')
      }
      const res = await bulkImportShortcuts(list)
      setImportNotice(
        `Imported ${res.count} (${res.inserted} new, ${res.updated} updated`
        + (res.skipped.length > 0 ? `, skipped ${res.skipped.length}` : '') + ').',
      )
      await reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Import failed')
    }
  }

  return (
    <section>
      <div className="flex items-center gap-3">
        <h2 className="text-xl font-semibold">Shortcuts</h2>
        <button
          type="button"
          onClick={() => { setDrawer({ mode: 'create' }); setDrawerError(null) }}
          className="ml-auto rounded bg-rd-accent px-3 py-1.5 text-sm text-rd-accent-ink"
        >
          New shortcut
        </button>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <input
          aria-label="Search shortcuts"
          placeholder="Search pattern or target…"
          value={query}
          onChange={(event) => onSearch(event.target.value)}
          className="rounded border border-rd-line bg-rd-input px-3 py-1.5 text-sm text-rd-text"
        />
        <select
          aria-label="Sort shortcuts"
          value={sort}
          onChange={(event) => onSort(event.target.value)}
          className="rounded border border-rd-line bg-rd-input px-2 py-1.5 text-sm text-rd-text"
        >
          <option value="updated_at">Recently updated</option>
          <option value="created_at">Recently created</option>
          <option value="popular">Most visited</option>
        </select>
        {selected.size > 0 && (
          confirmingBulk ? (
            <span className="flex gap-2">
              <button type="button" onClick={removeSelected} className="rounded bg-rd-danger px-3 py-1.5 text-sm text-rd-danger-ink">
                Confirm delete {selected.size}
              </button>
              <button type="button" onClick={() => setConfirmingBulk(false)} className="rounded border border-rd-line px-3 py-1.5 text-sm">
                Cancel
              </button>
            </span>
          ) : (
            <button type="button" onClick={() => setConfirmingBulk(true)} className="rounded border border-rd-danger px-3 py-1.5 text-sm text-rd-danger">
              Delete selected ({selected.size})
            </button>
          )
        )}
      </div>
      {error !== null && <p role="alert" className="mt-3 text-sm text-rd-danger">{error}</p>}
      {importNotice !== null && <p role="status" className="mt-3 text-sm text-rd-muted">{importNotice}</p>}
      {loading ? (
        <p className="mt-4 text-sm text-rd-muted">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="mt-4 text-sm text-rd-muted">No shortcuts found.</p>
      ) : (
        <div className="mt-3 overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-rd-line">
              <th className="py-1 pr-2">
                <input type="checkbox" aria-label="Select page" checked={pageSelected} onChange={togglePage} />
              </th>
              <th className="py-1 pr-2">Pattern</th>
              <th className="py-1 pr-2">Target</th>
              <th className="py-1 pr-2">Type</th>
              <th className="py-1 pr-2">Hits</th>
              <th className="py-1">Actions</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.pattern} className="border-b border-rd-line">
                <td className="py-1 pr-2">
                  <input
                    type="checkbox"
                    aria-label={`Select ${row.pattern}`}
                    checked={selected.has(row.pattern)}
                    onChange={() => toggle(row.pattern)}
                  />
                </td>
                <td className="py-1 pr-2 font-mono">{row.pattern}</td>
                <td className="max-w-xs truncate py-1 pr-2">{row.target}</td>
                <td className="py-1 pr-2">{row.type}</td>
                <td className="py-1 pr-2">{row.access_count}</td>
                <td className="py-1">
                  {confirming === row.pattern ? (
                    <span className="flex gap-2">
                      <button type="button" onClick={() => void removeOne(row.pattern)} className="text-rd-danger underline">
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
                        onClick={() => { setDrawer({ mode: 'edit', row }); setDrawerError(null) }}
                        className="underline text-rd-muted"
                      >
                        Edit
                      </button>
                      <button type="button" onClick={() => setConfirming(row.pattern)} className="underline text-rd-muted">
                        Delete
                      </button>
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}
      <div className="mt-3 flex items-center gap-3 text-sm">
        <button
          type="button"
          onClick={() => void exportAll()}
          className="rounded border border-rd-line px-3 py-1"
        >
          Export JSON
        </button>
        <label className="rounded border border-rd-line px-3 py-1">
          Import JSON
          <input
            type="file"
            accept="application/json,.json"
            className="hidden"
            aria-label="Import JSON file"
            onChange={(event) => {
              const file = event.target.files?.[0]
              event.target.value = ''
              if (file !== undefined) {
                void importFile(file)
              }
            }}
          />
        </label>
      </div>
      <div className="mt-3 flex items-center gap-3 text-sm">
        <button
          type="button"
          disabled={page <= 1}
          onClick={() => setPage((p) => Math.max(1, p - 1))}
          className="rounded border border-rd-line px-3 py-1 disabled:opacity-40"
        >
          Previous
        </button>
        <span className="text-rd-muted">
          Page {page} of {totalPages} · {total} total
        </span>
        <button
          type="button"
          disabled={page >= totalPages}
          onClick={() => setPage((p) => p + 1)}
          className="rounded border border-rd-line px-3 py-1 disabled:opacity-40"
        >
          Next
        </button>
      </div>
      {drawer !== null && (
        <>
        <div aria-hidden="true" onClick={() => setDrawer(null)} className="fixed inset-0 bg-black/40" />
        <div role="dialog" aria-label={drawer.mode === 'create' ? 'New shortcut' : 'Edit shortcut'} className="fixed right-0 top-0 h-full w-full max-w-md overflow-y-auto border-l border-rd-line bg-rd-surface p-4 text-rd-text shadow-xl">
          <h3 className="text-lg font-semibold">
            {drawer.mode === 'create' ? 'New shortcut' : `Edit ${drawer.row.pattern}`}
          </h3>
          <div className="mt-3">
            {drawer.mode === 'create' ? (
              <ShortcutForm
                submitLabel="Create"
                serverError={drawerError}
                onSubmit={saveDrawer}
                onCancel={() => setDrawer(null)}
              />
            ) : (
              <ShortcutForm
                initial={drawer.row}
                fixedPattern
                submitLabel="Save"
                serverError={drawerError}
                onSubmit={saveDrawer}
                onCancel={() => setDrawer(null)}
              />
            )}
            {drawer.mode === 'edit' && (
              <div className="mt-4 border-t border-rd-line pt-3">
                <p className="text-sm text-rd-muted">Share this link:</p>
                <img
                  src={`/qr/${encodeURIComponent(drawer.row.pattern)}`}
                  alt={`QR code for ${drawer.row.pattern}`}
                  width={160}
                  height={160}
                  className="mt-2 rounded border border-rd-line bg-white p-2"
                  loading="lazy"
                />
              </div>
            )}
          </div>
        </div>
        </>
      )}
    </section>
  )
}
