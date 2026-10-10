import { expect, test } from '@playwright/test'
import { API, loginAsAdmin, unique } from './helpers'

test('create upstream in the UI, live-check stream terminates', async ({ page, request }) => {
  await loginAsAdmin(page)
  const name = unique('e2eup')

  await page.goto('./upstreams')
  await page.getByLabel('Upstream name', { exact: true }).fill(name)
  await page.getByLabel('Base URL', { exact: true }).fill('https://example.com')
  await page.getByRole('button', { name: /add upstream/i }).click()
  await expect(page.getByText(name).first()).toBeVisible()

  const res = await request.get(`${API}/api/v1/upstreams/check/stream/${name}-probe`, {
    timeout: 60000,
  })
  expect(res.status()).toBe(200)
  expect(res.headers()['content-type']).toContain('text/event-stream')
  const body = await res.text()
  expect(body).toContain('"done": true')
})
