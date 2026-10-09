import { useState } from 'react'
import type { FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../lib/auth'

export default function Login() {
  const { login, verifyMfa } = useAuth()
  const navigate = useNavigate()
  const [password, setPassword] = useState('')
  const [pendingToken, setPendingToken] = useState<string | null>(null)
  const [totp, setTotp] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submitPassword(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const challenge = await login(password)
      if (challenge !== null) {
        setPendingToken(challenge.pending_token)
      } else {
        navigate('/', { replace: true })
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setBusy(false)
    }
  }

  async function submitTotp(event: FormEvent) {
    event.preventDefault()
    if (pendingToken === null) {
      return
    }
    setBusy(true)
    setError(null)
    try {
      await verifyMfa(pendingToken, totp)
      navigate('/', { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Verification failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-rd-bg p-4 font-sans text-rd-text">
      <div className="w-full max-w-sm rounded-2xl border border-rd-line bg-rd-surface p-6 shadow-xl">
        <h1 className="text-2xl font-bold">Redirector</h1>
        <p className="mt-1 text-sm text-rd-muted">
          {pendingToken === null ? 'Sign in with your admin password.' : 'Check your authenticator app.'}
        </p>
        {pendingToken === null ? (
        <form onSubmit={submitPassword} className="mt-6 flex flex-col gap-3">
          <label className="text-sm">
            Admin password
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="mt-1 w-full rounded border border-rd-line bg-rd-input px-3 py-2 text-sm text-rd-text"
            />
          </label>
          {error !== null && <p role="alert" className="text-sm text-rd-danger">{error}</p>}
          <button
            type="submit"
            disabled={busy || password.length === 0}
            className="rounded bg-rd-accent px-3 py-2 text-sm text-rd-accent-ink disabled:opacity-50"
          >
            Sign in
          </button>
        </form>
      ) : (
        <form onSubmit={submitTotp} className="mt-6 flex flex-col gap-3">
          <p className="text-sm text-rd-muted">
            Two-factor required — enter a code from your authenticator
            (or one unused backup code).
          </p>
          <label className="text-sm">
            Authentication code
            <input
              value={totp}
              onChange={(event) => setTotp(event.target.value)}
              inputMode="numeric"
              autoComplete="one-time-code"
              className="mt-1 w-full rounded border border-rd-line bg-rd-input px-3 py-2 font-mono text-sm text-rd-text"
            />
          </label>
          {error !== null && <p role="alert" className="text-sm text-rd-danger">{error}</p>}
          <button
            type="submit"
            disabled={busy || totp.length === 0}
            className="rounded bg-rd-accent px-3 py-2 text-sm text-rd-accent-ink disabled:opacity-50"
          >
            Verify
          </button>
        </form>
      )}
      </div>
    </main>
  )
}
