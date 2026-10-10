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

  // Step 1 auto-demos a sample target, then suggests a pattern from it.
  expect(
    await screen.findByDisplayValue('https://x.example/docs/getting-started', undefined, {
      timeout: 4000,
    }),
  ).toBeInTheDocument()
  expect(await screen.findByText('r/docs-getting-started', { exact: false })).toBeInTheDocument()

  // Step 2 auto-demos a pattern and checks it against the (mocked) API.
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify({ outcome: 'not_found' }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })),
  )
  await user.click(screen.getByRole('button', { name: 'Next' }))
  expect(screen.getByText('2 · Claim a name')).toBeInTheDocument()
  expect(
    await screen.findByDisplayValue('docs', undefined, { timeout: 4000 }),
  ).toBeInTheDocument()
  await screen.findByText(/available/, { exact: false }, { timeout: 4000 })

  // Steps advance with Back intact.
  await user.click(screen.getByRole('button', { name: 'Next' }))
  expect(screen.getByText('3 · Go dynamic')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Back' }))
  expect(screen.getByText('2 · Claim a name')).toBeInTheDocument()
})

test('typing takes over from the demo and it stays cancelled', async () => {
  const user = userEvent.setup()
  renderGuide()
  const input = screen.getByLabelText('Tutorial target')
  await user.type(input, 'https://mine.example/a')
  expect(input).toHaveValue('https://mine.example/a')
  // Past the demo window: nothing overwrites the user's own text.
  await new Promise((resolve) => setTimeout(resolve, 1500))
  expect(input).toHaveValue('https://mine.example/a')
})
