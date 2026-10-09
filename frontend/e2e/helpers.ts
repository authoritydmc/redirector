import { expect, type Page } from '@playwright/test'

export const E2E_ADMIN_PASSWORD = 'e2e-admin-password'
export const API = 'http://127.0.0.1:8123'

export function unique(prefix: string): string {
  return `${prefix}-${Date.now().toString(36)}-${Math.floor(Math.random() * 1e4)}`
}

export async function loginAsAdmin(page: Page): Promise<void> {
  await page.goto('./login')
  await page.getByLabel(/admin password/i).fill(E2E_ADMIN_PASSWORD)
  await page.getByRole('button', { name: /sign in/i }).click()
  await expect(page.getByRole('heading', { name: 'Shortcuts' })).toBeVisible()
}
