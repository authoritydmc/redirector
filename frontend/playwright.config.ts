import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  retries: process.env.CI ? 1 : 0,
  use: {
    // NOTE: `localhost`, not 127.0.0.1 — vite binds IPv6 [::1] on Windows.
    // Trailing slash: relative gotos ('./login') resolve under /.
    baseURL: 'http://localhost:5173/',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:5173/',
    reuseExistingServer: !process.env.CI,
    timeout: 120000,
  },
  globalSetup: './e2e/global-setup.ts',
  globalTeardown: './e2e/global-teardown.ts',
  projects: [
    { name: 'setup', testMatch: /.*\.setup\.ts/ },
    {
      name: 'chromium',
      dependencies: ['setup'],
      testIgnore: /.*\.setup\.ts|login\.spec\.ts/,
      use: { ...devices['Desktop Chrome'], storageState: 'e2e/.auth/user.json' },
    },
    // Login flows must start logged OUT, so they run without shared state.
    { name: 'login', testMatch: /login\.spec\.ts/ },
  ],
})
