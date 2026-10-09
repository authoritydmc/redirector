/** Theme registry: the single source of truth for rehearsed colorways.
 *
 * Each theme is a flat map of hex surfaces. The same values are wired into
 * CSS in `src/theme.css` (`[data-theme="<id>"]` blocks) and consumed via
 * Tailwind v4 `rd-*` utilities (`bg-rd-bg`, `text-rd-muted`, ...), which are
 * bound to the variables with `@theme inline` — so switching themes never
 * touches component code, and every pair below is contrast-tested in
 * `themes.test.ts` (WCAG AA: body text >= 7:1, everything else >= 4.5:1).
 */

export interface ThemeVars {
  /** Page background. */
  bg: string
  /** Raised surface (drawer, hover wash, table zebra if ever needed). */
  surface: string
  /** Primary body text. */
  text: string
  /** Secondary text. Must stay >= 4.5:1 on bg (no decorative grays). */
  muted: string
  /** Hairline borders. Decorative-only, but kept visible on both kinds. */
  line: string
  /** Input / select fill. */
  input: string
  /** Primary action fill (buttons, links-as-buttons, focus rings). */
  accent: string
  /** Text on top of accent. */
  accentInk: string
  /** Destructive text/borders. Per-kind (not one red) so it passes on dark. */
  danger: string
  /** Text on top of danger fill (bg value: the ratio is symmetric, so it passes). */
  dangerInk: string
}

export type ThemeKind = 'light' | 'dark'

export interface Theme {
  id: string
  label: string
  kind: ThemeKind
  vars: ThemeVars
}

function theme(id: string, label: string, kind: ThemeKind, vars: ThemeVars): Theme {
  return { id, label, kind, vars }
}

export const THEMES: Theme[] = [
  theme('light', 'Light', 'light', {
    bg: '#ffffff', surface: '#f6f8fa', text: '#1f2328', muted: '#59636e',
    line: '#d1d9e0', input: '#ffffff', accent: '#0969da', accentInk: '#ffffff',
    danger: '#cf222e', dangerInk: '#ffffff',
  }),
  theme('dark', 'Dark', 'dark', {
    bg: '#0f1221', surface: '#171b30', text: '#ffffff', muted: '#b9bdd1',
    line: '#2a2f45', input: '#1a1f36', accent: '#6e9fff', accentInk: '#0f1221',
    danger: '#f87171', dangerInk: '#0f1221',
  }),
  theme('github-light', 'GitHub Light', 'light', {
    bg: '#ffffff', surface: '#f6f8fa', text: '#1f2328', muted: '#59636e',
    line: '#d1d9e0', input: '#ffffff', accent: '#0969da', accentInk: '#ffffff',
    danger: '#cf222e', dangerInk: '#ffffff',
  }),
  theme('github-dark', 'GitHub Dark', 'dark', {
    bg: '#0d1117', surface: '#161b22', text: '#e6edf3', muted: '#9198a1',
    line: '#3d444d', input: '#0d1117', accent: '#4493f8', accentInk: '#06233f',
    danger: '#ff7b72', dangerInk: '#0d1117',
  }),
  theme('one-dark-pro', 'One Dark Pro', 'dark', {
    bg: '#282c34', surface: '#21252b', text: '#b8bec9', muted: '#9da5b4',
    line: '#3e4451', input: '#21252b', accent: '#61afef', accentInk: '#0b1c2c',
    danger: '#e5828a', dangerInk: '#282c34',
  }),
  theme('dracula', 'Dracula', 'dark', {
    bg: '#282a36', surface: '#21222c', text: '#f8f8f2', muted: '#c5c8d3',
    line: '#44475a', input: '#21222c', accent: '#bd93f9', accentInk: '#25173f',
    danger: '#ff5555', dangerInk: '#282a36',
  }),
  theme('nord', 'Nord', 'dark', {
    bg: '#2e3440', surface: '#3b4252', text: '#eceff4', muted: '#b6bfd2',
    line: '#4c566a', input: '#434c5e', accent: '#88c0d0', accentInk: '#1d2b33',
    danger: '#d29097', dangerInk: '#2e3440',
  }),
  theme('monokai', 'Monokai', 'dark', {
    bg: '#272822', surface: '#1e1f1c', text: '#f8f8f2', muted: '#c4c4b4',
    line: '#49483e', input: '#1e1f1c', accent: '#66d9ef', accentInk: '#12333b',
    danger: '#ff6188', dangerInk: '#272822',
  }),
  theme('solarized-light', 'Solarized Light', 'light', {
    bg: '#fdf6e3', surface: '#eee8d5', text: '#43575e', muted: '#56696f',
    line: '#d3c69f', input: '#fffdf5', accent: '#268bd2', accentInk: '#041e2e',
    danger: '#bb2a28', dangerInk: '#ffffff',
  }),
  theme('solarized-dark', 'Solarized Dark', 'dark', {
    bg: '#002b36', surface: '#073642', text: '#aeb8b8', muted: '#839496',
    line: '#1e5a6b', input: '#073642', accent: '#4aa3df', accentInk: '#002b36',
    danger: '#ff7b72', dangerInk: '#002b36',
  }),
  theme('tokyo-night', 'Tokyo Night', 'dark', {
    bg: '#1a1b26', surface: '#16161e', text: '#c0caf5', muted: '#a9b1d6',
    line: '#2f334d', input: '#16161e', accent: '#7aa2f7', accentInk: '#0d1330',
    danger: '#f7768e', dangerInk: '#1a1b26',
  }),
  theme('forest', 'Forest', 'dark', {
    bg: '#0d1f16', surface: '#12291d', text: '#e9f2ea', muted: '#a9c0ab',
    line: '#1e3d2a', input: '#0a1811', accent: '#4ade80', accentInk: '#06281a',
    danger: '#f87171', dangerInk: '#0d1f16',
  }),
  theme('sand-dunes', 'Sand Dunes', 'light', {
    bg: '#faf6ef', surface: '#f1e9d7', text: '#4a3f2f', muted: '#776549',
    line: '#ddd0b8', input: '#fffdf8', accent: '#92400a', accentInk: '#ffffff',
    danger: '#b91c1c', dangerInk: '#ffffff',
  }),
]

const BY_ID = new Map(THEMES.map((t) => [t.id, t]))

export type ThemeId = string

export function isThemeId(value: unknown): value is ThemeId {
  return typeof value === 'string' && BY_ID.has(value)
}

export function themeById(id: ThemeId): Theme {
  const found = BY_ID.get(id)
  if (found === undefined) {
    throw new Error(`unknown theme: ${id}`)
  }
  return found
}

/** Storage key shared with the legacy binary toggle ('dark'/'light' migrate). */
export const THEME_STORAGE_KEY = 'redirector.theme'
