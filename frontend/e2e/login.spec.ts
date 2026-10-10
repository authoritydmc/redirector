import { expect, test } from '@playwright/test'
import { loginAsAdmin } from './helpers'

test('wrong password shows an error', async ({ page }) => {
  await page.goto('./login')
  await page.getByLabel(/admin password/i).fill('nope')
  await page.getByRole('button', { name: /sign in/i }).click()
  await expect(page.getByRole('alert')).toHaveText('Incorrect password')
})

test('correct password lands on Shortcuts', async ({ page }) => {
  await loginAsAdmin(page)
  await expect(page).toHaveURL(/\/app\/?$/)
})
