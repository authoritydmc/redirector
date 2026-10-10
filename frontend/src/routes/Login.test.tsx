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
    expect(screen.getByText('Admin password')).toBeInTheDocument()
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
      expect(screen.getByText('Admin password')).toBeInTheDocument()
    })
  })
})
