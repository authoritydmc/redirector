import { expect, test } from '@playwright/test'
import { API, unique } from './helpers'

test('create shortcut in the UI, resolve it on the hot path', async ({ page, request }) => {
  await page.goto('./')
  const pattern = unique('e2e')
  const target = 'https://example.com/e2e'

  await page.getByRole('button', { name: /new shortcut/i }).click()
  await page.getByLabel('Pattern', { exact: true }).fill(pattern)
  await page.getByLabel('Target URL', { exact: true }).fill(target)
  await page.getByRole('button', { name: /^create$/i }).click()
  await expect(page.getByText(pattern).first()).toBeVisible()

  const res = await request.get(`${API}/${pattern}`, { maxRedirects: 0 })
  expect(res.status()).toBe(302)
  expect(res.headers()['location']).toBe(target)
})
