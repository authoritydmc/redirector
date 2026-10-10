import { expect, test } from '@playwright/test'

test('admin page shows all sections', async ({ page }) => {
  await page.goto('./admin')
  await expect(page.getByRole('heading', { name: 'Admin' })).toBeVisible()
  for (const section of ['Configuration', 'API keys', 'Two-factor auth', 'Backups']) {
    await expect(page.getByRole('heading', { name: section })).toBeVisible()
  }
})

test('metrics and guide pages render', async ({ page }) => {
  await page.goto('./metrics')
  await expect(page.getByRole('heading', { name: 'Metrics' })).toBeVisible()
  await page.goto('./guide')
  await expect(page.getByRole('heading', { name: 'Guide' })).toBeVisible()
})
