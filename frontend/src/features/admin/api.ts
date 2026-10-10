import { api } from '../../lib/client'
import type { components } from '../../lib/api'

export type Job = components['schemas']['JobRead']
export type Backup = components['schemas']['BackupRead']
export type ApiKey = components['schemas']['ApiKeyRead']
export type ApiKeyIssued = components['schemas']['ApiKeyIssued']
export type MfaStatus = components['schemas']['MfaStatusResponse']
export type MfaSetup = components['schemas']['MfaSetupResponse']
export type Kpi = components['schemas']['KpiResponse']
export type Live = components['schemas']['LiveResponse']
export type CacheEntry = components['schemas']['UpstreamCacheEntry']
export type CheckLog = components['schemas']['CheckLogEntry']

export interface AdminConfig {
  app_name?: string
  app_version?: string
  custom?: Record<string, unknown>
  [key: string]: unknown
}

export function listJobs(): Promise<Job[]> {
  return api<Job[]>('/api/v1/jobs')
}

export function cancelJob(id: number): Promise<Job> {
  return api<Job>(`/api/v1/jobs/${id}`, { method: 'DELETE' })
}

export function listBackups(): Promise<Backup[]> {
  return api<Backup[]>('/api/v1/admin/backup')
}

export function createBackup(label: string): Promise<Job> {
  return api<Job>('/api/v1/admin/backup', { method: 'POST', body: { label } })
}

export async function deleteBackup(name: string): Promise<void> {
  await api<unknown>(`/api/v1/admin/backup/${encodeURIComponent(name)}`, { method: 'DELETE' })
}

export function restoreBackup(name: string): Promise<Job> {
  return api<Job>(`/api/v1/admin/backup/${encodeURIComponent(name)}:restore`, { method: 'POST' })
}

export function backupDownloadUrl(name: string): string {
  return `/api/v1/admin/backup/${encodeURIComponent(name)}`
}

export function getConfig(): Promise<AdminConfig> {
  return api<AdminConfig>('/api/v1/admin/config')
}

export function patchConfig(settings: Record<string, unknown>): Promise<{ keys: string[] }> {
  return api<{ keys: string[] }>('/api/v1/admin/config', { method: 'PATCH', body: { settings } })
}

export function listApiKeys(): Promise<ApiKey[]> {
  return api<ApiKey[]>('/api/v1/auth/api-keys')
}

export function issueApiKey(name: string, scopes: string[]): Promise<ApiKeyIssued> {
  return api<ApiKeyIssued>('/api/v1/auth/api-keys', { method: 'POST', body: { name, scopes } })
}

export function revokeApiKey(id: number): Promise<ApiKey> {
  return api<ApiKey>(`/api/v1/auth/api-keys/${id}`, { method: 'DELETE' })
}

export function mfaStatus(): Promise<MfaStatus> {
  return api<MfaStatus>('/api/v1/auth/mfa/status')
}

export function mfaSetup(): Promise<MfaSetup> {
  return api<MfaSetup>('/api/v1/auth/mfa/setup', { method: 'POST' })
}

export function mfaEnable(token: string): Promise<{ backup_codes: string[] }> {
  return api<{ backup_codes: string[] }>('/api/v1/auth/mfa/enable', {
    method: 'POST',
    body: { token },
  })
}

export function mfaDisable(): Promise<MfaStatus> {
  return api<MfaStatus>('/api/v1/auth/mfa/disable', { method: 'POST' })
}

export function getKpi(): Promise<Kpi> {
  return api<Kpi>('/api/v1/metrics/kpi')
}

export function getLive(): Promise<Live> {
  return api<Live>('/api/v1/metrics/live')
}

export function listCache(upstream?: string): Promise<CacheEntry[]> {
  const query = upstream === undefined || upstream === '' ? '' : `?upstream=${encodeURIComponent(upstream)}`
  return api<CacheEntry[]>(`/api/v1/upstreams/cache${query}`)
}

export function purgeCache(upstream?: string): Promise<{ purged: number }> {
  const query = upstream === undefined || upstream === '' ? '' : `?upstream=${encodeURIComponent(upstream)}`
  return api<{ purged: number }>(`/api/v1/upstreams/cache${query}`, { method: 'DELETE' })
}

export function resyncCache(upstream: string, pattern?: string): Promise<{ updated: number; checked: number }> {
  return api<{ updated: number; checked: number }>('/api/v1/upstreams/cache/resync', {
    method: 'POST',
    body: { upstream, pattern: pattern ?? null },
  })
}

export function purgeCacheEntry(upstream: string, pattern: string): Promise<{ purged: number }> {
  return api<{ purged: number }>(
    `/api/v1/upstreams/cache/${encodeURIComponent(upstream)}/${encodeURIComponent(pattern)}`,
    { method: 'DELETE' },
  )
}

export function listCheckLogs(upstream?: string): Promise<CheckLog[]> {
  const query = upstream === undefined || upstream === '' ? '' : `?upstream=${encodeURIComponent(upstream)}`
  return api<CheckLog[]>(`/api/v1/upstreams/check-logs${query}`)
}

export function clearCheckLogs(upstream?: string): Promise<{ purged: number }> {
  const query = upstream === undefined || upstream === '' ? '' : `?upstream=${encodeURIComponent(upstream)}`
  return api<{ purged: number }>(`/api/v1/upstreams/check-logs${query}`, { method: 'DELETE' })
}
