import { expect, test as setup } from '@playwright/test'
import { E2E_ADMIN_PASSWORD } from './helpers'

const STATE_FILE = 'e2e/.auth/user.json'

setup('authenticate once for the run', async ({ page }) => {
  await page.goto('./login')
  await page.getByLabel(/admin password/i).fill(E2E_ADMIN_PASSWORD)
  await page.getByRole('button', { name: /sign in/i }).click()
  await expect(page.getByRole('heading', { name: 'Shortcuts' })).toBeVisible()
  await page.context().storageState({ path: STATE_FILE })
})
