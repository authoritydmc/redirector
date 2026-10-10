// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, test } from 'vitest'
import GuidePage from './GuidePage'

afterEach(() => {
  cleanup()
})

test('guide renders the five sections', () => {
  render(
    <MemoryRouter>
      <GuidePage />
    </MemoryRouter>,
  )
  expect(screen.getByRole('heading', { name: 'Guide' })).toBeInTheDocument()
  expect(screen.getByText('1 · Shortcuts')).toBeInTheDocument()
  expect(screen.getByText('5 · Automation')).toBeInTheDocument()
})
