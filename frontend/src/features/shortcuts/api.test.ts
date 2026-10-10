import { describe, expect, it } from 'vitest'
import { hasDynamicPlaceholder, suggestPatternFor } from './api'

describe('suggestPatternFor', () => {
  it('derives a slug from the last path segments', () => {
    expect(suggestPatternFor('https://x.example/docs/getting-started')).toBe('docs-getting-started')
    expect(suggestPatternFor('https://x.example/docs')).toBe('docs')
  })

  it('falls back to the host when there is no path', () => {
    expect(suggestPatternFor('https://google.com')).toBe('google')
    expect(suggestPatternFor('https://www.example.com/')).toBe('example')
    expect(suggestPatternFor('http://intranet')).toBe('intranet')
    expect(suggestPatternFor('http://127.0.0.1:8123/x')).toBe('x')
  })

  it('returns empty for garbage input', () => {
    expect(suggestPatternFor('not a url')).toBe('')
    expect(suggestPatternFor('')).toBe('')
  })
})

describe('hasDynamicPlaceholder', () => {
  it('spots brace and arg placeholders', () => {
    expect(hasDynamicPlaceholder('https://j.example/{ticket}')).toBe(true)
    expect(hasDynamicPlaceholder('https://x.example/d/[arg]')).toBe(true)
    expect(hasDynamicPlaceholder('https://x.example/docs')).toBe(false)
  })
})
