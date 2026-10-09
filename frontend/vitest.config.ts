import { defineConfig } from 'vitest/config'

// Playwright specs live in e2e/ and must never load under vitest (the two
// `test()` globals collide). Playwright is scoped via testDir in
// playwright.config.ts; this is the mirror rule.
export default defineConfig({
  test: {
    exclude: ['e2e/**', 'node_modules/**'],
  },
})
