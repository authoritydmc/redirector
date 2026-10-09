/** Contrast audit: every theme's functional pairs must pass WCAG AA.
 *
 * - body text/bg >= 7:1 (we hold ourselves above the 4.5 AA floor)
 * - muted/accent-ink/danger(-ink) on their fills >= 4.5:1
 * - registry integrity: unique ids, all vars present + well-formed hex
 * - CSS coverage: every registered id has a `[data-theme]` block in theme.css
 *
 * If a pair fails here, adjust the palette in themes.ts + theme.css together.
 */

import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { THEMES, type ThemeVars } from './themes'

function channel(value: number): number {
  const sRGB = value / 255
  return sRGB <= 0.03928 ? sRGB / 12.92 : Math.pow((sRGB + 0.055) / 1.055, 2.4)
}

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => channel(parseInt(hex.slice(i, i + 2), 16)))
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

/** WCAG contrast ratio for two hex colors (order-independent). */
export function contrastRatio(a: string, b: string): number {
  const [lighter, darker] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (lighter + 0.05) / (darker + 0.05)
}

const HEX = /^#[0-9a-f]{6}$/i
const VAR_KEYS: Array<keyof ThemeVars> = [
  'bg', 'surface', 'text', 'muted', 'line',
  'input', 'accent', 'accentInk', 'danger', 'dangerInk',
]

describe('theme registry', () => {
  it('has unique ids and complete, well-formed palettes', () => {
    const ids = THEMES.map((t) => t.id)
    expect(new Set(ids).size).toBe(ids.length)
    expect(ids.length).toBeGreaterThanOrEqual(13)
    for (const theme of THEMES) {
      expect(theme.kind === 'light' || theme.kind === 'dark').toBe(true)
      for (const key of VAR_KEYS) {
        expect(HEX.test(theme.vars[key]), `${theme.id}.${key}=${theme.vars[key]}`).toBe(true)
      }
    }
  })

  it('covers every theme in theme.css', () => {
    // Raw on-disk CSS (not the bundler-processed copy): selectors keep
    // their authored quotes here.
    const css = readFileSync(new URL('../theme.css', import.meta.url), 'utf-8')
    for (const theme of THEMES) {
      expect(
        css.includes(`[data-theme='${theme.id}']`),
        `missing CSS block: ${theme.id}`,
      ).toBe(true)
    }
  })
})

describe.each(THEMES.map((t) => [t.label, t.vars] as const))(
  'contrast: %s',
  (_label, vars) => {
    it('body text on bg >= 7:1', () => {
      expect(contrastRatio(vars.text, vars.bg)).toBeGreaterThanOrEqual(7)
    })
    it('muted on bg >= 4.5:1', () => {
      expect(contrastRatio(vars.muted, vars.bg)).toBeGreaterThanOrEqual(4.5)
    })
    it('accent ink on accent >= 4.5:1', () => {
      expect(contrastRatio(vars.accentInk, vars.accent)).toBeGreaterThanOrEqual(4.5)
    })
    it('danger on bg >= 4.5:1', () => {
      expect(contrastRatio(vars.danger, vars.bg)).toBeGreaterThanOrEqual(4.5)
    })
    it('danger ink on danger >= 4.5:1', () => {
      expect(contrastRatio(vars.dangerInk, vars.danger)).toBeGreaterThanOrEqual(4.5)
    })
  },
)
