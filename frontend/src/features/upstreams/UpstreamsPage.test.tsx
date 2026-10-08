// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from '../../lib/auth'
import type { Upstream } from './api'
import UpstreamsPage from './UpstreamsPage'

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

class FakeSource {
  static instances: FakeSource[] = []
  onmessage: ((event: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  closed = false

  constructor(public url: string) {
    FakeSource.instances.push(this)
  }

  close() {
    this.closed = true
  }

  emit(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) })
  }
}

function renderPage() {
  localStorage.setItem('redirector.token', 'jwt-test')
  return render(
    <AuthProvider>
      <UpstreamsPage />
    </AuthProvider>,
  )
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  localStorage.clear()
  FakeSource.instances = []
})

const wiki: Upstream = {
  id: 7,
  name: 'wiki',
  base_url: 'https://wiki.example',
  fail_url: null,
  fail_status_code: 404,
  verify_ssl: true,
  skip_sso_cache: false,
}

describe('UpstreamsPage', () => {
  it('renders rows and creates entries', async () => {
    const user = userEvent.setup()
    const calls = mockFetch((call) => {
      if (call.method === 'POST') {
        return jsonResponse(201, { ...wiki, name: 'new' })
      }
      return jsonResponse(200, [wiki])
    })
    renderPage()
    await screen.findByText('wiki')
    await user.type(screen.getByLabelText(/upstream name/i), 'new')
    await user.type(screen.getByLabelText(/^base url$/i), 'https://new.example')
    await user.click(screen.getByRole('button', { name: /add upstream/i }))
    await waitFor(() => {
      const posted = calls.find((call) => call.method === 'POST')
      expect(posted?.body).toMatchObject({ name: 'new', base_url: 'https://new.example' })
    })
  })

  it('deletes after inline confirm', async () => {
    const user = userEvent.setup()
    const calls = mockFetch((call) => {
      if (call.method === 'DELETE') {
        return new Response(null, { status: 204 })
      }
      return jsonResponse(200, [wiki])
    })
    renderPage()
    await screen.findByText('wiki')
    await user.click(screen.getByRole('button', { name: /^delete$/i }))
    await user.click(screen.getByRole('button', { name: /confirm/i }))
    await waitFor(() => {
      expect(calls.some((call) => call.method === 'DELETE' && call.url.endsWith('/7'))).toBe(true)
    })
  })

  it('streams a live check to done', async () => {
    const user = userEvent.setup()
    mockFetch(() => jsonResponse(200, [wiki]))
    vi.stubGlobal('EventSource', FakeSource)
    renderPage()
    await screen.findByText('wiki')
    await user.type(screen.getByLabelText(/pattern to check/i), 'stream-me')
    await user.click(screen.getByRole('button', { name: /^check$/i }))
    expect(FakeSource.instances).toHaveLength(1)
    expect(FakeSource.instances[0].url).toContain('/stream-me')

    const source = FakeSource.instances[0]
    source.emit({ message: 'Starting check for pattern: stream-me' })
    source.emit({ upstream_name: 'wiki', status: 'found', target_url: 'https://wiki.example/stream-me' })
    source.emit({ done: true })
    await waitFor(() => {
      expect(screen.getByText('done')).toBeInTheDocument()
    })
    expect(source.closed).toBe(true)
  })

  it('surfaces stream disconnects', async () => {
    const user = userEvent.setup()
    mockFetch(() => jsonResponse(200, [wiki]))
    vi.stubGlobal('EventSource', FakeSource)
    renderPage()
    await screen.findByText('wiki')
    await user.type(screen.getByLabelText(/pattern to check/i), 'x')
    await user.click(screen.getByRole('button', { name: /^check$/i }))
    const source = FakeSource.instances[0]
    source.onerror?.()
    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/disconnected/i)
    })
  })
})
