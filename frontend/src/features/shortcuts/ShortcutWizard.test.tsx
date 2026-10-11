// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ShortcutWizard from './ShortcutWizard'
import type { ShortcutInput } from './api'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function stubResolve(outcome: string, target: string | null = null) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => jsonResponse(200, { outcome, target })),
  )
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('ShortcutWizard', () => {
  it('walks kind, target, name, review and creates', async () => {
    const user = userEvent.setup()
    stubResolve('not_found')
    let submitted: ShortcutInput | null = null
    render(
      <ShortcutWizard
        serverError={null}
        onSubmit={async (input) => {
          submitted = input
        }}
      />,
    )
    // Step 1: kind.
    await user.click(screen.getByRole('radio', { name: /dynamic link/i }))
    await user.click(screen.getByRole('button', { name: 'Next' }))
    // Step 2: target.
    await user.type(screen.getByLabelText('Wizard target URL'), 'https://j.example/t')
    expect(await screen.findByText(/suggested name/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Next' }))
    // Step 3: name (suggestion available, pattern free).
    await user.click(screen.getByRole('button', { name: /use “t”/i }))
    await waitFor(() => {
      expect(screen.getByText(/available ✓/)).toBeInTheDocument()
    })
    await user.click(screen.getByRole('button', { name: 'Next' }))
    // Step 4: review + create.
    expect(screen.getByText('r/t')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Create link' }))
    await waitFor(() => {
      expect(submitted).toMatchObject({ pattern: 't', target: 'https://j.example/t', type: 'dynamic' })
    })
  })

  it('blocks Next on a taken name', async () => {
    const user = userEvent.setup()
    stubResolve('redirect', 'https://taken.example')
    let submitted: ShortcutInput | null = null
    render(
      <ShortcutWizard
        serverError={null}
        onSubmit={async (input) => {
          submitted = input
        }}
      />,
    )
    await user.click(screen.getByRole('button', { name: 'Next' }))
    await user.type(screen.getByLabelText('Wizard target URL'), 'https://x.example/y')
    await user.click(screen.getByRole('button', { name: 'Next' }))
    await user.type(screen.getByLabelText('Wizard pattern'), 'taken')
    await waitFor(() => {
      expect(screen.getByText(/already taken/)).toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
    expect(submitted).toBeNull()
  })
})
