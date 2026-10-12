import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../lib/auth'
import { api } from '../lib/client'

interface SsoProvider {
  id: string
  display_name: string
}

const SSO_ERRORS: Record<string, string> = {
  'unknown-provider': 'That sign-in provider is not configured.',
  'invalid-state': 'Sign-in expired or was already used — please try again.',
  'bad-discovery': 'The identity provider is unreachable right now.',
  'invalid-token': 'The identity provider response was invalid — please try again.',
  'bad-claims': 'The identity provider response was incomplete.',
  'not-authorized': 'Your account is not in an admin group for this install.',
}

function readFragment(hash: string): { token: string | null; error: string | null } {
  const params = new URLSearchParams(hash.startsWith('#') ? hash.slice(1) : hash)
  return { token: params.get('access_token'), error: params.get('error') }
}

export default function Login() {
  const { login, verifyMfa, loginWithToken, loginUser, signup } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [password, setPassword] = useState('')
  const [pendingToken, setPendingToken] = useState<string | null>(null)
  const [totp, setTotp] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [providers, setProviders] = useState<SsoProvider[]>([])
  const [tab, setTab] = useState<'admin' | 'account'>('admin')
  const [accountMode, setAccountMode] = useState<'signin' | 'signup'>('signin')
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [userPassword, setUserPassword] = useState('')

  useEffect(() => {
    api<SsoProvider[]>('/api/v1/auth/providers').then(setProviders, () => setProviders([]))
  }, [])

  // OIDC landing: the API redirects here with #access_token=... (adopt it)
  // or #error=<code> (explain it). Runs once per fragment.
  useEffect(() => {
    const { token, error: code } = readFragment(location.hash)
    if (token !== null && token !== '') {
      loginWithToken(token)
      navigate('/', { replace: true })
    } else if (code !== null && code !== '') {
      setError(SSO_ERRORS[code] ?? 'Single sign-on failed — please try again.')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.hash])

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

  async function submitAccount(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      if (accountMode === 'signup') {
        await signup(email.trim(), name.trim(), userPassword)
        await loginUser(email.trim(), userPassword)
      } else {
        await loginUser(email.trim(), userPassword)
      }
      navigate('/', { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign-in failed')
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
        <div className="mt-3 flex gap-2" role="tablist" aria-label="Sign-in method">
          {(['admin', 'account'] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              role="tab"
              aria-selected={tab === mode && pendingToken === null}
              onClick={() => { setTab(mode); setError(null) }}
              className={`rounded border px-3 py-1 text-xs ${
                tab === mode && pendingToken === null
                  ? 'border-rd-accent bg-rd-accent text-rd-accent-ink'
                  : 'border-rd-line text-rd-muted'
              }`}
            >
              {mode === 'admin' ? 'Admin password' : 'Team account'}
            </button>
          ))}
        </div>
        {pendingToken === null && tab === 'account' ? (
        <form onSubmit={submitAccount} className="mt-6 flex flex-col gap-3">
          <p className="text-sm text-rd-muted">
            {accountMode === 'signin'
              ? 'Sign in with your team account.'
              : 'Create a team account — you can join teams right away.'}
          </p>
          {accountMode === 'signup' && (
            <label className="text-sm">
              Display name
              <input
                value={name}
                onChange={(event) => setName(event.target.value)}
                autoComplete="nickname"
                className="mt-1 w-full rounded border border-rd-line bg-rd-input px-3 py-2 text-sm text-rd-text"
              />
            </label>
          )}
          <label className="text-sm">
            Email
            <input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              autoComplete="email"
              className="mt-1 w-full rounded border border-rd-line bg-rd-input px-3 py-2 text-sm text-rd-text"
            />
          </label>
          <label className="text-sm">
            Password{accountMode === 'signup' ? ' (8+ characters)' : ''}
            <input
              type="password"
              value={userPassword}
              onChange={(event) => setUserPassword(event.target.value)}
              autoComplete={accountMode === 'signup' ? 'new-password' : 'current-password'}
              className="mt-1 w-full rounded border border-rd-line bg-rd-input px-3 py-2 text-sm text-rd-text"
            />
          </label>
          {error !== null && <p role="alert" className="text-sm text-rd-danger">{error}</p>}
          <button
            type="submit"
            disabled={busy || email.trim() === '' || userPassword === ''}
            className="rounded bg-rd-accent px-3 py-2 text-sm text-rd-accent-ink disabled:opacity-50"
          >
            {accountMode === 'signin' ? 'Sign in' : 'Sign up'}
          </button>
          <button
            type="button"
            onClick={() => {
              setAccountMode(accountMode === 'signin' ? 'signup' : 'signin')
              setError(null)
            }}
            className="text-sm underline text-rd-muted"
          >
            {accountMode === 'signin' ? 'Need an account? Sign up' : 'Have an account? Sign in'}
          </button>
        </form>
        ) : pendingToken === null ? (
        <form onSubmit={submitPassword} className="mt-6 flex flex-col gap-3">
          <p className="text-sm text-rd-muted">Sign in with the admin password.</p>
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
        {pendingToken === null && providers.length > 0 && (
          <div className="mt-6 border-t border-rd-line pt-4">
            <p className="text-sm text-rd-muted">Or continue with</p>
            <div className="mt-2 flex flex-col gap-2">
              {providers.map((provider) => (
                <a
                  key={provider.id}
                  href={`/api/v1/auth/oidc/${provider.id}/login`}
                  className="rounded border border-rd-line px-3 py-2 text-center text-sm hover:bg-rd-surface"
                >
                  Continue with {provider.display_name}
                </a>
              ))}
            </div>
          </div>
        )}
      </div>
    </main>
  )
}
