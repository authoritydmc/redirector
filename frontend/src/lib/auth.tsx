import { createContext, useCallback, useContext, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import type { components } from './api.ts'

type TokenResponse = components['schemas']['TokenResponse']

const STORAGE_KEY = 'redirector.token'

interface AuthState {
  token: string | null
  login: (password: string) => Promise<{ pending_token: string } | null>
  verifyMfa: (pendingToken: string, totp: string) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthState | null>(null)

async function postJson<T>(path: string, body: unknown): Promise<{ status: number; data: T }> {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return { status: res.status, data: (await res.json()) as T }
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

  const value = useMemo(
    () => ({ token, login, verifyMfa, logout }),
    [token, login, verifyMfa, logout],
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
