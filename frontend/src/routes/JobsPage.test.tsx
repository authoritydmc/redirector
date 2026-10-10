// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from '../lib/auth'
import JobsPage from './JobsPage'

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
      <JobsPage />
    </AuthProvider>,
  )
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  localStorage.clear()
})

describe('JobsPage', () => {
  it('renders job rows with cancel for live ones', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (String(url).includes('/api/v1/jobs')) {
          return jsonResponse(200, [
            { id: 1, kind: 'backup_create', status: 'running', total: 10, done: 4, error: null },
            { id: 2, kind: 'resync', status: 'succeeded', total: 3, done: 3, error: null },
          ])
        }
        return jsonResponse(404, {})
      }),
    )
    renderPage()
    await waitFor(() => expect(screen.getByText('backup_create')).toBeInTheDocument())
    expect(screen.getByText('4/10')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument()
  })

  it('shows the empty state with no jobs', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, [])))
    renderPage()
    await waitFor(() => expect(screen.getByText(/no jobs yet/i)).toBeInTheDocument())
  })
})
