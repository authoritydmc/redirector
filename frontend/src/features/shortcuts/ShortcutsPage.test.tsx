// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from '../../lib/auth'
import type { Shortcut } from './api'
import ShortcutsPage from './ShortcutsPage'

function row(pattern: string, hits = 3): Shortcut {
  return {
    id: 1,
    pattern,
    type: 'static',
    target: `https://${pattern}.example/x`,
    access_count: hits,
    tags: [],
    visibility: 'public',
    expires_at: null,
    owner_email: null,
  }
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

interface Call {
  url: string
  method: string
  body?: unknown
}

function mockFetch(handler: (call: Call) => Response) {
  const calls: Call[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      const call: Call = { url, method: init?.method ?? 'GET' }
      if (typeof init?.body === 'string') {
        call.body = JSON.parse(init.body) as unknown
      }
      calls.push(call)
      return handler(call)
    }),
  )
  return calls
}

function renderPage() {
  localStorage.setItem('redirector.token', 'jwt-test')
  return render(
    <AuthProvider>
      <ShortcutsPage />
    </AuthProvider>,
  )
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  localStorage.clear()
})

const listBody = (patterns: string[], total: number) => ({
  data: patterns.map((pattern) => row(pattern)),
  meta: { page: 1, pageSize: 20, total },
})

describe('ShortcutsPage', () => {
  it('renders rows from the list endpoint', async () => {
    mockFetch(() => jsonResponse(200, listBody(['docs', 'gh'], 2)))
    renderPage()
    await waitFor(() => {
      expect(screen.getByText('docs')).toBeInTheDocument()
    })
    expect(screen.getByText('gh')).toBeInTheDocument()
    expect(screen.getByText('Page 1 of 1 · 2 total')).toBeInTheDocument()
  })

  it('searches with a debounced query', async () => {
    const user = userEvent.setup()
    const calls = mockFetch(() => jsonResponse(200, listBody(['docs'], 1)))
    renderPage()
    await screen.findByText('docs')
    await user.type(screen.getByLabelText(/search shortcuts/i), 'do')
    await waitFor(() => {
      expect(calls.some((call) => call.url.includes('q=do'))).toBe(true)
    })
  })

  it('pages through results', async () => {
    const user = userEvent.setup()
    mockFetch((call) => {
      const page = new URL(call.url, 'http://x').searchParams.get('page')
      return jsonResponse(
        200,
        page === '2' ? listBody(['tail'], 21) : listBody(['head'], 21),
      )
    })
    renderPage()
    await screen.findByText('head')
    await user.click(screen.getByRole('button', { name: /next/i }))
    await waitFor(() => {
      expect(screen.getByText('tail')).toBeInTheDocument()
    })
    expect(screen.getByText('Page 2 of 2 · 21 total')).toBeInTheDocument()
  })

  it('deletes a row after inline confirm', async () => {
    const user = userEvent.setup()
    const calls = mockFetch((call) => {
      if (call.method === 'DELETE') {
        return new Response(null, { status: 204 })
      }
      return jsonResponse(200, listBody(['gone'], 1))
    })
    renderPage()
    await screen.findByText('gone')
    await user.click(screen.getByRole('button', { name: /^delete$/i }))
    await user.click(screen.getByRole('button', { name: /confirm/i }))
    await waitFor(() => {
      expect(calls.some((call) => call.method === 'DELETE' && call.url.endsWith('/gone'))).toBe(true)
    })
  })

  it('bulk deletes selected rows', async () => {
    const user = userEvent.setup()
    const calls = mockFetch((call) => {
      if (call.method === 'POST') {
        return jsonResponse(200, { deleted: ['a', 'b'], not_found: [] })
      }
      return jsonResponse(200, listBody(['a', 'b'], 2))
    })
    renderPage()
    await screen.findByText('a')
    await user.click(screen.getByLabelText('Select a'))
    await user.click(screen.getByLabelText('Select b'))
    await user.click(screen.getByRole('button', { name: /delete selected \(2\)/i }))
    await user.click(screen.getByRole('button', { name: /confirm delete 2/i }))
    await waitFor(() => {
      const posted = calls.find((call) => call.method === 'POST')
      expect(posted?.body).toEqual({ patterns: ['a', 'b'] })
    })
  })

  it('shows API errors', async () => {
    mockFetch(() => jsonResponse(500, { title: 'boom' }))
    renderPage()
    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument()
    })
  })

  it('validates the form before posting', async () => {
    const user = userEvent.setup()
    const calls = mockFetch(() => jsonResponse(200, listBody([], 0)))
    renderPage()
    await screen.findByText('No shortcuts found.')
    await user.click(screen.getByRole('button', { name: /new shortcut/i }))
    await user.type(screen.getByLabelText('Target URL'), 'not-a-url')
    await user.click(screen.getByRole('button', { name: /^create$/i }))
    await waitFor(() => {
      expect(screen.getByText(/must start with http/i)).toBeInTheDocument()
    })
    expect(calls.some((call) => call.method === 'POST')).toBe(false)
  })

  it('creates a shortcut through the drawer', async () => {
    const user = userEvent.setup()
    const calls = mockFetch((call) => {
      if (call.method === 'POST') {
        return jsonResponse(201, row('brand-new'))
      }
      return jsonResponse(200, listBody([], 0))
    })
    renderPage()
    await screen.findByText('No shortcuts found.')
    await user.click(screen.getByRole('button', { name: /new shortcut/i }))
    await user.type(screen.getByLabelText(/^pattern$/i), 'brand-new')
    await user.type(screen.getByLabelText(/target url/i), 'https://x.example/new')
    await user.click(screen.getByRole('button', { name: /^create$/i }))
    await waitFor(() => {
      const posted = calls.find((call) => call.method === 'POST')
      expect(posted?.body).toMatchObject({ pattern: 'brand-new', target: 'https://x.example/new' })
    })
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
  })

  it('edits a row with the pattern locked', async () => {
    const user = userEvent.setup()
    const calls = mockFetch((call) => {
      if (call.method === 'PATCH') {
        return jsonResponse(200, row('docs'))
      }
      return jsonResponse(200, listBody(['docs'], 1))
    })
    renderPage()
    await screen.findByText('docs')
    await user.click(screen.getByRole('button', { name: /^edit$/i }))
    const patternInput = screen.getByLabelText(/^pattern$/i) as HTMLInputElement
    expect(patternInput).toBeDisabled()
    expect(patternInput.value).toBe('docs')
    const target = screen.getByLabelText(/target url/i)
    await user.clear(target)
    await user.type(target, 'https://x.example/changed')
    await user.click(screen.getByRole('button', { name: /^save$/i }))
    await waitFor(() => {
      const patched = calls.find((call) => call.method === 'PATCH')
      expect(patched?.url).toContain('/docs')
      expect(patched?.body).toMatchObject({ target: 'https://x.example/changed' })
    })
  })

  it('hides import when logged out but keeps export public', async () => {
    mockFetch(() => jsonResponse(200, listBody(['docs'], 1)))
    render(
      <AuthProvider>
        <ShortcutsPage />
      </AuthProvider>,
    )
    await screen.findByText('docs')
    expect(screen.getByRole('button', { name: /export json/i })).toBeInTheDocument()
    expect(screen.queryByLabelText(/import json file/i)).not.toBeInTheDocument()
  })

  it('shows import/export when logged in', async () => {
    mockFetch(() => jsonResponse(200, listBody(['docs'], 1)))
    renderPage()
    await screen.findByText('docs')
    expect(screen.getByRole('button', { name: /export json/i })).toBeInTheDocument()
    expect(screen.getByLabelText(/import json file/i)).toBeInTheDocument()
  })
})
