// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '../App'
import { AuthProvider } from '../lib/auth'

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </MemoryRouter>,
  )
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  localStorage.clear()
})

describe('Login', () => {
  it('renders the password form', () => {
    renderAt('/login')
    expect(screen.getByRole('tab', { name: 'Admin password' })).toBeInTheDocument()
    expect(screen.getByLabelText(/admin password/i)).toBeInTheDocument()
  })

  it('shows an error on wrong password', async () => {
    const user = userEvent.setup()
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(401, { code: 'auth:bad-credentials' })))
    renderAt('/login')
    await user.type(screen.getByLabelText(/admin password/i), 'nope')
    await user.click(screen.getByRole('button', { name: /sign in/i }))
    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('Incorrect password')
    })
  })

  it('signs in and lands on the dashboard', async () => {
    const user = userEvent.setup()
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse(200, { access_token: 'jwt-test', role: 'admin' })),
    )
    renderAt('/login')
    await user.type(screen.getByLabelText(/admin password/i), 'right')
    await user.click(screen.getByRole('button', { name: /sign in/i }))
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Shortcuts' })).toBeInTheDocument()
    })
    expect(localStorage.getItem('redirector.token')).toBe('jwt-test')
  })

  it('shows public shortcuts to anonymous visitors', async () => {
    renderAt('/')
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Shortcuts' })).toBeInTheDocument()
    })
  })

  it('redirects anonymous visitors to login for admin routes', async () => {
    renderAt('/admin')
    await waitFor(() => {
      expect(screen.getByLabelText(/admin password/i)).toBeInTheDocument()
    })
  })

  it('shows SSO provider buttons when configured', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: unknown) => {
        if (String(url).includes('/api/v1/auth/providers')) {
          return jsonResponse(200, [{ id: 'google', display_name: 'Google' }])
        }
        return jsonResponse(404, {})
      }),
    )
    renderAt('/login')
    const button = await screen.findByRole('link', { name: /continue with google/i })
    expect(button).toHaveAttribute('href', '/api/v1/auth/oidc/google/login')
  })

  it('adopts an SSO token from the URL fragment', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: unknown) => {
        if (String(url).includes('/api/v1/auth/providers')) {
          return jsonResponse(200, [])
        }
        return jsonResponse(200, { data: [], meta: { total: 0 } })
      }),
    )
    renderAt('/login#access_token=sso-jwt-test')
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Shortcuts' })).toBeInTheDocument()
    })
    expect(localStorage.getItem('redirector.token')).toBe('sso-jwt-test')
  })

  it('explains SSO errors from the URL fragment', async () => {
    renderAt('/login#error=not-authorized')
    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/not in an admin group/i)
    })
  })

  it('signs up a team account and lands on the dashboard', async () => {
    const user = userEvent.setup()
    const calls: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push(`${init?.method ?? 'GET'} ${String(url)}`)
        if (String(url).includes('/api/v1/auth/signup')) {
          return jsonResponse(201, { id: 1, email: 'amy@x.com', name: 'Amy' })
        }
        if (String(url).includes('/api/v1/auth/user/login')) {
          return jsonResponse(200, { access_token: 'jwt-user', role: 'user' })
        }
        if (String(url).includes('/api/v1/auth/providers')) {
          return jsonResponse(200, [])
        }
        return jsonResponse(200, { data: [], meta: { total: 0 } })
      }),
    )
    renderAt('/login')
    await user.click(screen.getByRole('tab', { name: /team account/i }))
    await user.click(screen.getByRole('button', { name: /need an account/i }))
    await user.type(screen.getByLabelText('Display name'), 'Amy')
    await user.type(screen.getByLabelText('Email'), 'amy@x.com')
    await user.type(screen.getByLabelText(/password \(8\+ characters\)/i), 'password123')
    await user.click(screen.getByRole('button', { name: /^sign up$/i }))
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Shortcuts' })).toBeInTheDocument()
    })
    expect(localStorage.getItem('redirector.token')).toBe('jwt-user')
    expect(calls).toContain('POST /api/v1/auth/signup')
  })
})
