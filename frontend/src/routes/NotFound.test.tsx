// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { expect, test } from 'vitest'
import NotFound from './NotFound'

test('unknown route renders the 404 page with a way back', () => {
  render(
    <MemoryRouter>
      <NotFound />
    </MemoryRouter>,
  )
  expect(screen.getByRole('heading', { name: 'Page not found' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Back to Shortcuts' })).toHaveAttribute('href', '/')
})
