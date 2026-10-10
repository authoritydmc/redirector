// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from '../lib/auth'
import AdminPage from './AdminPage'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function renderPage() {
  localStorage.setItem('redirector.token', 'jwt-test')
  return render(
    <AuthProvider>
      <AdminPage />
    </AuthProvider>,
  )
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  localStorage.clear()
})

describe('AdminPage', () => {
  it('renders all sections with live data', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        const path = String(url)
        if (path.includes('/api/v1/admin/config')) {
          return jsonResponse(200, { app_name: 'redirector', app_version: '4.0.0', custom: {} })
        }
        if (path.includes('/api/v1/auth/api-keys')) {
          return jsonResponse(200, [])
        }
        if (path.includes('/api/v1/auth/mfa/status')) {
          return jsonResponse(200, { enabled: false, backup_codes_remaining: 0 })
        }
        if (path.includes('/api/v1/admin/backup')) {
          return jsonResponse(200, [])
        }
        return jsonResponse(404, {})
      }),
    )
    renderPage()
    await waitFor(() => expect(screen.getByText('Configuration')).toBeInTheDocument())
    expect(screen.getByText('API keys')).toBeInTheDocument()
    expect(screen.getByText('Two-factor auth')).toBeInTheDocument()
    expect(screen.getByText('Backups')).toBeInTheDocument()
  })

  it('issues a key and shows it once', async () => {
    const user = userEvent.setup()
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        const path = String(url)
        if (path.includes('/api/v1/auth/api-keys') && init?.method === 'POST') {
          return jsonResponse(201, {
            id: 1, name: 'cron', prefix: 'rk_abc', scopes: ['*'],
            created_at: 'now', last_used_at: null, revoked_at: null,
            api_key: 'rk_abc_SECRET',
          })
        }
        if (path.includes('/api/v1/admin/config')) {
          return jsonResponse(200, { app_name: 'redirector', app_version: '4.0.0', custom: {} })
        }
        return jsonResponse(200, [])
      }),
    )
    renderPage()
    await user.type(screen.getByLabelText('Key name'), 'cron')
    await user.click(screen.getByRole('button', { name: /issue key/i }))
    await waitFor(() => expect(screen.getByText(/rk_abc_SECRET/)).toBeInTheDocument())
  })
})
