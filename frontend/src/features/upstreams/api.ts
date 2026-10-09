import { api } from '../../lib/client'
import type { components } from '../../lib/api'

export type Upstream = components['schemas']['UpstreamRead']

export interface UpstreamInput {
  name: string
  base_url: string
  fail_url?: string | null
  fail_status_code?: number | null
}

export function listUpstreams(): Promise<Upstream[]> {
  return api<Upstream[]>('/api/v1/upstreams')
}

export function createUpstream(input: UpstreamInput): Promise<Upstream> {
  return api<Upstream>('/api/v1/upstreams', { method: 'POST', body: input })
}

export async function deleteUpstream(id: number): Promise<void> {
  await api<void>(`/api/v1/upstreams/${id}`, { method: 'DELETE' })
}

export interface CheckEvent {
  upstream_name?: string
  check_url?: string
  status?: string
  target_url?: string
  message?: string
  done?: boolean
}
