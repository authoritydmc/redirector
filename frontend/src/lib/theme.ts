import { useEffect, useState } from 'react'
import { THEMES, THEME_STORAGE_KEY, isThemeId, type ThemeId } from './themes'

function initialTheme(): ThemeId {
  const stored = localStorage.getItem(THEME_STORAGE_KEY)
  // Legacy binary toggle values migrate to the same-named themes.
  // Any stored id (including the retired 'redirector' default) is respected;
  // only first-time visitors fall through to the clay default.
  if (stored === 'dark' || stored === 'light' || isThemeId(stored)) {
    return stored
  }
  // Signature look out of the box — not the OS default.
  return 'clay-cream'
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
