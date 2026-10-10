// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from '../lib/auth'
import MetricsPage from './MetricsPage'

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
      <MetricsPage />
    </AuthProvider>,
  )
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  localStorage.clear()
})

describe('MetricsPage', () => {
  it('renders KPI and live cards', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        const path = String(url)
        if (path.includes('/api/v1/metrics/kpi')) {
          return jsonResponse(200, {
            overview: {
              total_shortcuts: 3, total_hits: 15, avg_hits: 5, zero_hit_count: 1,
              most_popular: { pattern: 'docs' },
            },
          })
        }
        return jsonResponse(200, {
          cache: { hits: 4, misses: 2, hit_rate: 66.7 },
          counts: { total_shortcuts: 3, total_hits: 15 },
          process: { cpu_percent: 3.1, memory_mb: 88.2, platform: 'test', python: '3.12' },
          status: 'ready',
        })
      }),
    )
    renderPage()
    await waitFor(() => expect(screen.getByText('Total hits')).toBeInTheDocument())
    expect(screen.getByText('15')).toBeInTheDocument()
    expect(screen.getByText('docs')).toBeInTheDocument()
    expect(screen.getByText('66.7%')).toBeInTheDocument()
  })
})
