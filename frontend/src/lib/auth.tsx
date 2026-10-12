import { createContext, useCallback, useContext, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import type { components } from './api.ts'

type TokenResponse = components['schemas']['TokenResponse']

const STORAGE_KEY = 'redirector.token'

interface AuthState {
  token: string | null
  role: string | null
  email: string | null
  login: (password: string) => Promise<{ pending_token: string } | null>
  verifyMfa: (pendingToken: string, totp: string) => Promise<void>
  loginUser: (email: string, password: string) => Promise<void>
  signup: (email: string, name: string, password: string) => Promise<void>
  loginWithToken: (token: string) => void
  logout: () => void
}

const AuthContext = createContext<AuthState | null>(null)

async function postJson<T>(path: string, body: unknown): Promise<{ status: number; data: T }> {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  // Read as text first: proxies and 500 pages are rarely JSON, and a bare
  // res.json() would surface a confusing SyntaxError instead of the status.
  const text = await res.text()
  let data: T
  try {
    data = JSON.parse(text) as T
  } catch {
    throw new Error(`Request failed (HTTP ${res.status})`)
  }
  return { status: res.status, data }
}

/** UI-only identity from the JWT payload (never verified client-side —
 * the server enforces everything; this just hides buttons you can't use). */
function identityOf(token: string | null): { role: string | null; email: string | null } {
  if (token === null) {
    return { role: null, email: null }
  }
  try {
    const payload = JSON.parse(atob(token.split('.')[1])) as { role?: unknown; sub?: unknown }
    const role = typeof payload.role === 'string' ? payload.role : null
    const sub = typeof payload.sub === 'string' ? payload.sub : null
    return { role, email: role === 'user' ? sub : null }
  } catch {
    return { role: null, email: null }
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem(STORAGE_KEY))

  const logout = useCallback(() => {
    localStorage.removeItem(STORAGE_KEY)
    setToken(null)
  }, [])

  const login = useCallback(async (password: string) => {
    type LoginResult = TokenResponse | { mfa_required: true; pending_token: string }
    const { status, data } = await postJson<LoginResult>('/api/v1/auth/login', { password })
    if (status === 401) {
      throw new Error('Incorrect password')
    }
    if (status === 429) {
      throw new Error('Too many attempts — try again later')
    }
    if ('pending_token' in data) {
      return { pending_token: data.pending_token }
    }
    if (status !== 200) {
      throw new Error(`Login failed (HTTP ${status})`)
    }
    localStorage.setItem(STORAGE_KEY, data.access_token)
    setToken(data.access_token)
    return null
  }, [])

  const verifyMfa = useCallback(async (pendingToken: string, totp: string) => {
    const { status, data } = await postJson<TokenResponse>('/api/v1/auth/mfa/verify', {
      pending_token: pendingToken,
      token: totp,
    })
    if (status !== 200 || !('access_token' in data)) {
      throw new Error('Incorrect code — try again')
    }
    localStorage.setItem(STORAGE_KEY, data.access_token)
    setToken(data.access_token)
  }, [])

  const loginWithToken = useCallback((token: string) => {
    localStorage.setItem(STORAGE_KEY, token)
    setToken(token)
  }, [])

  const loginUser = useCallback(async (email: string, password: string) => {
    const { status, data } = await postJson<TokenResponse>('/api/v1/auth/user/login', {
      email,
      password,
    })
    if (status === 401) {
      throw new Error('Incorrect email or password')
    }
    if (status === 403) {
      throw new Error('This account is disabled — ask an admin')
    }
    if (status === 429) {
      throw new Error('Too many attempts — try again later')
    }
    if (status !== 200 || !('access_token' in data)) {
      throw new Error(`Login failed (HTTP ${status})`)
    }
    loginWithToken(data.access_token)
  }, [loginWithToken])

  const signup = useCallback(async (email: string, name: string, password: string) => {
    const { status, data } = await postJson<{ detail?: string }>('/api/v1/auth/signup', {
      email,
      name,
      password,
    })
    if (status === 409) {
      throw new Error('That email already has an account — sign in instead')
    }
    if (status === 422) {
      throw new Error(
        typeof data.detail === 'string' ? data.detail : 'Check the email and password',
      )
    }
    if (status !== 201) {
      throw new Error(`Sign-up failed (HTTP ${status})`)
    }
  }, [])

  const { role, email } = useMemo(() => identityOf(token), [token])

  const value = useMemo(
    () => ({ token, role, email, login, verifyMfa, loginUser, signup, loginWithToken, logout }),
    [token, role, email, login, verifyMfa, loginUser, signup, loginWithToken, logout],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthState {
  const state = useContext(AuthContext)
  if (state === null) {
    throw new Error('useAuth must be used inside <AuthProvider>')
  }
  return state
}
