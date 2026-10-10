// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import GuidePage from './GuidePage'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function renderGuide() {
  return render(
    <MemoryRouter>
      <GuidePage />
    </MemoryRouter>,
  )
}

test('tutorial walks five live steps', async () => {
  const user = userEvent.setup()
  renderGuide()
  expect(screen.getByRole('heading', { name: 'Guide' })).toBeInTheDocument()
  expect(screen.getByText('1 · Point it somewhere')).toBeInTheDocument()

  // Step 1 suggests a pattern from the target, locally.
  await user.type(screen.getByLabelText('Tutorial target'), 'https://x.example/docs')
  expect(await screen.findByText('r/docs', { exact: false })).toBeInTheDocument()

  // Step 2 checks availability against the (mocked) API.
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify({ outcome: 'not_found' }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })),
  )
  await user.click(screen.getByRole('button', { name: 'Next' }))
  expect(screen.getByText('2 · Claim a name')).toBeInTheDocument()
  await user.type(screen.getByLabelText('Tutorial pattern'), 'fresh-link')
  await screen.findByText(/available/, { exact: false })

  // Steps advance with Back intact.
  await user.click(screen.getByRole('button', { name: 'Next' }))
  expect(screen.getByText('3 · Go dynamic')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Back' }))
  expect(screen.getByText('2 · Claim a name')).toBeInTheDocument()
})
