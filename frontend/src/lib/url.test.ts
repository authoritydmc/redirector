import { describe, expect, it } from 'vitest'
import { isExternalUrl, joinPath } from './url'

describe('isExternalUrl', () => {
  it('accepts absolute http(s) only', () => {
    expect(isExternalUrl('https://x.example/docs')).toBe(true)
    expect(isExternalUrl('http://go/docs')).toBe(true)
    expect(isExternalUrl('javascript:alert(1)')).toBe(false)
    expect(isExternalUrl('data:text/html,hi')).toBe(false)
    expect(isExternalUrl('/relative/path')).toBe(false)
    expect(isExternalUrl('not a url')).toBe(false)
  })
})

describe('joinPath', () => {
  it('joins segments with single slashes', () => {
    expect(joinPath('a/', '/b', 'c/')).toBe('a/b/c')
    expect(joinPath('/api/v1/', '/shortcuts')).toBe('/api/v1/shortcuts')
    expect(joinPath('', 'x')).toBe('x')
  })
})
