// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from '../../lib/auth'
import TeamsPage from './TeamsPage'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function renderTeams() {
  localStorage.setItem(
    'redirector.token',
    `x.${btoa(JSON.stringify({ role: 'user', sub: 'amy@x.com' }))}.y`,
  )
  return render(
    <MemoryRouter initialEntries={['/teams']}>
      <AuthProvider>
        <TeamsPage />
      </AuthProvider>
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  localStorage.clear()
})

describe('TeamsPage', () => {
  it('lists teams with members and creates one', async () => {
    const user = userEvent.setup()
    const calls: Array<{ url: string; method: string; body?: unknown }> = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({
          url: String(url),
          method: init?.method ?? 'GET',
          body: typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : undefined,
        })
        if (String(url).includes('/api/v1/teams') && (init?.method ?? 'GET') === 'POST') {
          return jsonResponse(201, {
            id: 2, name: 'blue', created_by_email: 'amy@x.com', created_at: 'now', members: [],
          })
        }
        return jsonResponse(200, [
          {
            id: 1, name: 'red', created_by_email: 'amy@x.com', created_at: 'now',
            members: [{ email: 'amy@x.com', role: 'admin', created_at: 'now' }],
          },
        ])
      }),
    )
    renderTeams()
    expect(await screen.findByText('red')).toBeInTheDocument()
    expect(screen.getByText('amy@x.com')).toBeInTheDocument()
    await user.type(screen.getByLabelText('New team name'), 'blue')
    await user.click(screen.getByRole('button', { name: /create team/i }))
    await waitFor(() => {
      const posted = calls.find(
        (call) => call.url.includes('/api/v1/teams') && call.method === 'POST',
      )
      expect(posted?.body).toMatchObject({ name: 'blue' })
    })
    expect(await screen.findByText(/team blue created/i)).toBeInTheDocument()
  })

  it('adds a member by email', async () => {
    const user = userEvent.setup()
    const calls: Array<{ url: string; method: string; body?: unknown }> = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({
          url: String(url),
          method: init?.method ?? 'GET',
          body: typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : undefined,
        })
        if (String(url).includes('/members') && (init?.method ?? 'GET') === 'POST') {
          return jsonResponse(201, { email: 'bob@x.com', role: 'member', created_at: 'now' })
        }
        return jsonResponse(200, [
          {
            id: 1, name: 'red', created_by_email: 'amy@x.com', created_at: 'now',
            members: [{ email: 'amy@x.com', role: 'admin', created_at: 'now' }],
          },
        ])
      }),
    )
    renderTeams()
    await screen.findByText('red')
    await user.type(screen.getByLabelText('Add member to red'), 'bob@x.com')
    await user.click(screen.getByRole('button', { name: /^add$/i }))
    await waitFor(() => {
      const posted = calls.find((call) => call.url.includes('/members') && call.method === 'POST')
      expect(posted?.body).toMatchObject({ email: 'bob@x.com' })
    })
  })
})
