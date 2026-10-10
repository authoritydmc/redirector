import { useCallback, useEffect, useState } from 'react'
import { ApiError } from '../lib/client'
import QRCode from 'react-qr-code'
import {
  createBackup,
  deleteBackup,
  getConfig,
  issueApiKey,
  listApiKeys,
  listBackups,
  mfaDisable,
  mfaEnable,
  mfaSetup,
  mfaStatus,
  patchConfig,
  restoreBackup,
  revokeApiKey,
  type ApiKey,
  type Backup,
  type ConfigSchemaItem,
  type MfaSetup,
  type MfaStatus,
} from '../features/admin/api'

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-6 rounded-xl border border-rd-line bg-rd-surface p-4 shadow-xl">
      <h3 className="text-lg font-semibold">{title}</h3>
      <div className="mt-3">{children}</div>
    </section>
  )
}

function useAdminError(): [string | null, (err: unknown, fallback: string) => void, () => void] {
  const [error, setError] = useState<string | null>(null)
  const fail = useCallback((err: unknown, fallback: string) => {
    setError(
      err instanceof ApiError && err.status === 401
        ? 'Session expired — sign in again'
        : err instanceof Error
          ? err.message
          : fallback,
    )
  }, [])
  const clear = useCallback(() => setError(null), [])
  return [error, fail, clear]
}

function ConfigSection() {
  const [schema, setSchema] = useState<ConfigSchemaItem[]>([])
  const [values, setValues] = useState<Record<string, string>>({})
  const [custom, setCustom] = useState('')
  const [appInfo, setAppInfo] = useState<{ app_name?: unknown; app_version?: unknown }>({})
  const [saved, setSaved] = useState<string | null>(null)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [error, fail, clear] = useAdminError()

  const load = useCallback(async () => {
    try {
      const loaded = await getConfig()
      const items = Array.isArray(loaded.schema) ? loaded.schema : []
      setSchema(items)
      const next: Record<string, string> = {}
      for (const item of items) {
        if (!item.readonly && item.value !== null && item.value !== undefined) {
          next[item.key] = String(item.value)
        }
      }
      setValues(next)
      setCustom(JSON.stringify(loaded.custom ?? {}, null, 2))
      setAppInfo({ app_name: loaded.app_name, app_version: loaded.app_version })
    } catch (err) {
      fail(err, 'Failed to load config')
    }
  }, [fail])

  useEffect(() => {
    void load()
  }, [load])

  function dirtyKeys(): Record<string, unknown> {
    const out: Record<string, unknown> = {}
    for (const item of schema) {
      if (item.readonly) {
        continue
      }
      const raw = values[item.key]
      if (raw === undefined) {
        continue
      }
      const current = item.value === null || item.value === undefined ? '' : String(item.value)
      if (raw !== current) {
        out[item.key] = item.type === 'int' ? Number(raw) : raw
      }
    }
    return out
  }

  async function save() {
    clear()
    setSaved(null)
    const changes = dirtyKeys()
    if (Object.keys(changes).length === 0) {
      setSaved('Nothing to save.')
      return
    }
    try {
      const res = await patchConfig(changes)
      setSaved(`Saved: ${res.keys.join(', ')}`)
      await load()
    } catch (err) {
      fail(err, 'Save failed')
    }
  }

  async function saveAdvanced() {
    clear()
    setSaved(null)
    let parsed: Record<string, unknown>
    try {
      parsed = JSON.parse(custom) as Record<string, unknown>
    } catch {
      fail(new Error('Advanced JSON must be valid'), 'Invalid JSON')
      return
    }
    try {
      const res = await patchConfig(parsed)
      setSaved(`Saved: ${res.keys.join(', ') || 'nothing to save'}`)
      await load()
    } catch (err) {
      fail(err, 'Save failed')
    }
  }

  const editable = schema.filter((item) => !item.readonly && item.type !== 'secret')
  const envManaged = schema.filter((item) => item.readonly)

  return (
    <div>
      <p className="text-sm text-rd-muted">
        App <span className="font-mono">{String(appInfo.app_name ?? '?')}</span>
        {' · '}version <span className="font-mono">{String(appInfo.app_version ?? '?')}</span>.
        Changes apply immediately — no restart, except environment items.
      </p>
      {error !== null && <p role="alert" className="mt-2 text-sm text-rd-danger">{error}</p>}
      {editable.map((item) => (
        <label key={item.key} className="mt-3 flex flex-col text-sm">
          <span>
            {item.title}{' '}
            <span className="rounded bg-rd-surface px-1.5 py-0.5 font-mono text-xs text-rd-muted">
              {item.source}
            </span>
          </span>
          <span className="text-xs text-rd-muted">{item.description}</span>
          <span className="text-xs text-rd-muted">
            {item.min !== null && item.min !== undefined && item.max !== null && item.max !== undefined
              ? `Range ${item.min}–${item.max}. `
              : ''}
            {item.env_var !== null && item.env_var !== undefined ? `Env override: ${item.env_var}.` : ''}
          </span>
          <input
            aria-label={item.title}
            value={values[item.key] ?? ''}
            onChange={(event) => setValues((prev) => ({ ...prev, [item.key]: event.target.value }))}
            inputMode={item.type === 'int' ? 'numeric' : undefined}
            className="mt-1 w-full rounded border border-rd-line bg-rd-input px-3 py-1.5 text-sm text-rd-text"
          />
        </label>
      ))}
      <div className="mt-3">
        <button
          type="button"
          onClick={() => void save()}
          className="rounded bg-rd-accent px-3 py-1.5 text-sm text-rd-accent-ink"
        >
          Save settings
        </button>
        {saved !== null && <span className="ml-3 text-sm text-rd-muted">{saved}</span>}
      </div>
      {envManaged.length > 0 && (
        <div className="mt-4">
          <p className="text-sm font-semibold">Environment-only (restart to change)</p>
          <ul className="mt-1 flex flex-col gap-1 text-sm">
            {envManaged.map((item) => (
              <li key={item.key} className="rounded border border-rd-line px-2 py-1">
                <span className="font-semibold">{item.title}</span>
                <span className="text-rd-muted"> — {item.description}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="mt-4">
        <button
          type="button"
          onClick={() => setShowAdvanced((v) => !v)}
          className="text-sm underline text-rd-muted"
          aria-expanded={showAdvanced}
        >
          {showAdvanced ? 'Hide advanced JSON' : 'Show advanced JSON'}
        </button>
        {showAdvanced && (
          <div className="mt-2">
            <textarea
              aria-label="Advanced settings JSON"
              value={custom}
              onChange={(event) => setCustom(event.target.value)}
              rows={6}
              spellCheck={false}
              className="w-full rounded border border-rd-line bg-rd-input px-3 py-2 font-mono text-xs text-rd-text"
            />
            <button
              type="button"
              onClick={() => void saveAdvanced()}
              className="mt-2 rounded border border-rd-line px-3 py-1.5 text-sm"
            >
              Save advanced JSON
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

function KeysSection() {
  const [rows, setRows] = useState<ApiKey[]>([])
  const [name, setName] = useState('')
  const [issued, setIssued] = useState<string | null>(null)
  const [error, fail, clear] = useAdminError()

  const reload = useCallback(async () => {
    try {
      setRows(await listApiKeys())
    } catch (err) {
      fail(err, 'Failed to load API keys')
    }
  }, [fail])

  useEffect(() => {
    void reload()
  }, [reload])

  async function issue() {
    if (name.trim() === '') {
      return
    }
    clear()
    try {
      const created = await issueApiKey(name.trim(), ['*'])
      setIssued(created.api_key)
      setName('')
      await reload()
    } catch (err) {
      fail(err, 'Issue failed')
    }
  }

  async function revoke(id: number) {
    clear()
    try {
      await revokeApiKey(id)
      await reload()
    } catch (err) {
      fail(err, 'Revoke failed')
    }
  }

  return (
    <div>
      {error !== null && <p role="alert" className="text-sm text-rd-danger">{error}</p>}
      {issued !== null && (
        <p role="status" className="mt-2 rounded border border-rd-line bg-rd-input p-2 font-mono text-xs break-all">
          New key (shown once): {issued}
        </p>
      )}
      <div className="mt-2 flex flex-wrap items-end gap-2">
        <label className="flex flex-col text-sm">
          Key name
          <input
            aria-label="Key name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            className="mt-1 rounded border border-rd-line bg-rd-input px-3 py-1.5 text-sm text-rd-text"
          />
        </label>
        <button
          type="button"
          onClick={() => void issue()}
          disabled={name.trim() === ''}
          className="rounded bg-rd-accent px-3 py-1.5 text-sm text-rd-accent-ink disabled:opacity-50"
        >
          Issue key
        </button>
      </div>
      {rows.length > 0 && (
        <ul className="mt-3 flex flex-col gap-1 text-sm">
          {rows.map((row) => (
            <li key={row.id} className="flex items-center gap-2 rounded border border-rd-line px-2 py-1 font-mono text-xs">
              <span>{row.name}</span>
              <span className="text-rd-muted">{row.prefix}…</span>
              {row.revoked_at !== null && row.revoked_at !== undefined && (
                <span className="text-rd-danger">revoked</span>
              )}
              {(row.revoked_at === null || row.revoked_at === undefined) && (
                <button type="button" onClick={() => void revoke(row.id)} className="ml-auto underline text-rd-muted">
                  Revoke
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function MfaSection() {
  const [status, setStatus] = useState<MfaStatus | null>(null)
  const [setup, setSetup] = useState<MfaSetup | null>(null)
  const [token, setToken] = useState('')
  const [codes, setCodes] = useState<string[] | null>(null)
  const [error, fail, clear] = useAdminError()

  const reload = useCallback(async () => {
    try {
      setStatus(await mfaStatus())
    } catch (err) {
      fail(err, 'Failed to load MFA status')
    }
  }, [fail])

  useEffect(() => {
    void reload()
  }, [reload])

  async function start() {
    clear()
    try {
      setSetup(await mfaSetup())
      setCodes(null)
    } catch (err) {
      fail(err, 'Setup failed')
    }
  }

  async function enable() {
    clear()
    try {
      const res = await mfaEnable(token.trim())
      setCodes(res.backup_codes)
      setSetup(null)
      setToken('')
      await reload()
    } catch (err) {
      fail(err, 'Enable failed — check the code')
    }
  }

  async function disable() {
    clear()
    try {
      await mfaDisable()
      setSetup(null)
      setCodes(null)
      await reload()
    } catch (err) {
      fail(err, 'Disable failed')
    }
  }

  return (
    <div>
      {error !== null && <p role="alert" className="text-sm text-rd-danger">{error}</p>}
      <p className="text-sm">
        Status:{' '}
        <strong>{status === null ? '…' : status.enabled ? 'enabled' : 'disabled'}</strong>
        {status !== null && status.enabled && (
          <span className="text-rd-muted"> · {status.backup_codes_remaining} backup codes left</span>
        )}
      </p>
      {codes !== null && (
        <p role="status" className="mt-2 rounded border border-rd-line bg-rd-input p-2 font-mono text-xs break-all">
          Backup codes (store now, shown once): {codes.join(' · ')}
        </p>
      )}
      {setup !== null ? (
        <div className="mt-2">
          <div className="flex flex-wrap items-start gap-3">
            <div className="rounded border border-rd-line bg-white p-2">
              <QRCode value={setup.otpauth_url} size={160} aria-label="TOTP setup QR code" />
            </div>
            <p className="max-w-sm flex-1 rounded border border-rd-line bg-rd-input p-2 font-mono text-xs break-all">
              Seed (manual entry): {setup.secret}
            </p>
          </div>
          <div className="mt-2 flex flex-wrap items-end gap-2">
            <label className="flex flex-col text-sm">
              6-digit code
              <input
                aria-label="6-digit code"
                value={token}
                onChange={(event) => setToken(event.target.value)}
                inputMode="numeric"
                className="mt-1 rounded border border-rd-line bg-rd-input px-3 py-1.5 font-mono text-sm text-rd-text"
              />
            </label>
            <button
              type="button"
              onClick={() => void enable()}
              disabled={token.trim() === ''}
              className="rounded bg-rd-accent px-3 py-1.5 text-sm text-rd-accent-ink disabled:opacity-50"
            >
              Enable
            </button>
          </div>
        </div>
      ) : (
        <div className="mt-2 flex gap-2">
          {!status?.enabled && (
            <button type="button" onClick={() => void start()} className="rounded bg-rd-accent px-3 py-1.5 text-sm text-rd-accent-ink">
              Start setup
            </button>
          )}
          {status?.enabled && (
            <button type="button" onClick={() => void disable()} className="rounded border border-rd-danger px-3 py-1.5 text-sm text-rd-danger">
              Disable MFA
            </button>
          )}
        </div>
      )}
    </div>
  )
}

function BackupsSection() {
  const [rows, setRows] = useState<Backup[]>([])
  const [label, setLabel] = useState('')
  const [notice, setNotice] = useState<string | null>(null)
  const [error, fail, clear] = useAdminError()

  const reload = useCallback(async () => {
    try {
      setRows(await listBackups())
    } catch (err) {
      fail(err, 'Failed to load backups')
    }
  }, [fail])

  useEffect(() => {
    void reload()
  }, [reload])

  async function create() {
    clear()
    setNotice(null)
    try {
      const job = await createBackup(label.trim() === '' ? 'manual' : label.trim())
      setNotice(`Backup job #${job.id} enqueued — watch it on the Jobs page.`)
      setLabel('')
      await reload()
    } catch (err) {
      fail(err, 'Create failed')
    }
  }

  async function remove(name: string) {
    clear()
    try {
      await deleteBackup(name)
      await reload()
    } catch (err) {
      fail(err, 'Delete failed')
    }
  }

  async function restore(name: string) {
    clear()
    try {
      const job = await restoreBackup(name)
      setNotice(`Restore of ${name} staged as job #${job.id}.`)
      await reload()
    } catch (err) {
      fail(err, 'Restore failed')
    }
  }

  return (
    <div>
      {error !== null && <p role="alert" className="text-sm text-rd-danger">{error}</p>}
      {notice !== null && <p role="status" className="text-sm text-rd-muted">{notice}</p>}
      <div className="mt-2 flex flex-wrap items-end gap-2">
        <label className="flex flex-col text-sm">
          Label
          <input
            aria-label="Backup label"
            value={label}
            placeholder="before change"
            onChange={(event) => setLabel(event.target.value)}
            className="mt-1 rounded border border-rd-line bg-rd-input px-3 py-1.5 text-sm text-rd-text"
          />
        </label>
        <button
          type="button"
          onClick={() => void create()}
          className="rounded bg-rd-accent px-3 py-1.5 text-sm text-rd-accent-ink"
        >
          Create backup
        </button>
      </div>
      {rows.length > 0 && (
        <div className="mt-3 overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-rd-line">
              <th className="py-1 pr-2">Name</th>
              <th className="py-1 pr-2">Size</th>
              <th className="py-1 pr-2">Created</th>
              <th className="py-1">Actions</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.name} className="border-b border-rd-line">
                <td className="py-1 pr-2 font-mono">{row.name}</td>
                <td className="py-1 pr-2">{(row.size_bytes / 1024).toFixed(1)} KB</td>
                <td className="py-1 pr-2">{row.created_at}</td>
                <td className="py-1">
                  <span className="flex gap-2">
                    <a className="underline text-rd-muted" href={`/api/v1/admin/backup/${encodeURIComponent(row.name)}`}>
                      Download
                    </a>
                    <button type="button" onClick={() => void restore(row.name)} className="underline text-rd-muted">
                      Restore
                    </button>
                    <button type="button" onClick={() => void remove(row.name)} className="underline text-rd-danger">
                      Delete
                    </button>
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}
    </div>
  )
}

export default function AdminPage() {
  return (
    <div>
      <h2 className="text-xl font-semibold">Admin</h2>
      <Section title="Configuration">
        <ConfigSection />
      </Section>
      <Section title="API keys">
        <KeysSection />
      </Section>
      <Section title="Two-factor auth">
        <MfaSection />
      </Section>
      <Section title="Backups">
        <BackupsSection />
      </Section>
    </div>
  )
}
