import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  retries: process.env.CI ? 1 : 0,
  use: {
    // NOTE: `localhost`, not 127.0.0.1 — vite binds IPv6 [::1] on Windows.
    // Trailing slash: relative gotos ('./login') resolve under /app/.
    baseURL: 'http://localhost:5173/app/',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:5173/app/',
    reuseExistingServer: !process.env.CI,
    timeout: 120000,
  },
  globalSetup: './e2e/global-setup.ts',
  globalTeardown: './e2e/global-teardown.ts',
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
