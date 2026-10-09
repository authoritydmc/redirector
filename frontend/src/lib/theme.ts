import { useEffect, useState } from 'react'
import { THEMES, THEME_STORAGE_KEY, isThemeId, type ThemeId } from './themes'

function systemTheme(): ThemeId {
  if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  }
  return 'light'
}

function initialTheme(): ThemeId {
  const stored = localStorage.getItem(THEME_STORAGE_KEY)
  // Legacy binary toggle values migrate to the same-named themes.
  if (stored === 'dark' || stored === 'light' || isThemeId(stored)) {
    return stored
  }
  return systemTheme()
}

/** Active theme id, persisted; flips `document.documentElement.dataset.theme`. */
export function useTheme(): [ThemeId, (id: ThemeId) => void] {
  const [theme, setTheme] = useState<ThemeId>(initialTheme)
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem(THEME_STORAGE_KEY, theme)
  }, [theme])
  return [theme, setTheme]
}

export { THEMES, THEME_STORAGE_KEY, isThemeId, type ThemeId }
