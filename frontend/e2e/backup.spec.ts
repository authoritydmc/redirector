import { expect, test } from '@playwright/test'
import { API, E2E_ADMIN_PASSWORD } from './helpers'

test('backup lifecycle through the API: enqueue, drain, list', async ({ request }) => {
  const login = await request.post(`${API}/api/v1/auth/login`, {
    data: { password: E2E_ADMIN_PASSWORD },
  })
  expect(login.status()).toBe(200)
  const headers = { Authorization: `Bearer ${(await login.json()).access_token}` }

  const enqueued = await request.post(`${API}/api/v1/admin/backup`, {
    headers,
    data: { label: 'e2e' },
  })
  expect(enqueued.status()).toBe(202)
  const jobId = (await enqueued.json()).id as number

  await expect
    .poll(
      async () => {
        const status = await request.get(`${API}/api/v1/jobs/${jobId}`, { headers })
        return (await status.json()).status as string
      },
      { timeout: 60000 },
    )
    .toBe('succeeded')

  const listed = await request.get(`${API}/api/v1/admin/backup`, { headers })
  expect(listed.status()).toBe(200)
  const names = ((await listed.json()) as Array<{ name: string }>).map((b) => b.name)
  expect(names.some((name) => name.endsWith('-e2e.zip'))).toBe(true)
})
