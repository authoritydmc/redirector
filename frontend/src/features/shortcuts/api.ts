import { api } from '../../lib/client'
import type { components } from '../../lib/api'

export type Shortcut = components['schemas']['ShortcutRead']
export type ShortcutList = components['schemas']['ShortcutListResponse']
export type BulkDeleteResult = components['schemas']['BulkDeleteResponse']
export type ShortcutType = components['schemas']['ShortcutType']
export type Visibility = components['schemas']['Visibility']

export interface ListParams {
  page: number
  pageSize: number
  q: string
  sort: string
}

export function listShortcuts(params: ListParams): Promise<ShortcutList> {
  const query = new URLSearchParams({
    page: String(params.page),
    pageSize: String(params.pageSize),
    q: params.q,
    sort: params.sort,
  })
  return api<ShortcutList>(`/api/v1/shortcuts?${query.toString()}`)
}

export async function deleteShortcut(pattern: string): Promise<void> {
  await api<void>(`/api/v1/shortcuts/${encodeURIComponent(pattern)}`, { method: 'DELETE' })
}

export function bulkDeleteShortcuts(patterns: string[]): Promise<BulkDeleteResult> {
  return api<BulkDeleteResult>('/api/v1/shortcuts/bulk-delete', {
    method: 'POST',
    body: { patterns },
  })
}

export interface BulkImportResult {
  inserted: number
  updated: number
  skipped: string[]
  count: number
}

export function bulkImportShortcuts(shortcuts: ShortcutInput[]): Promise<BulkImportResult> {
  return api<BulkImportResult>('/api/v1/shortcuts/bulk-import', {
    method: 'POST',
    body: { shortcuts },
  })
}

export interface ShortcutInput {
  pattern: string
  target: string
  type: ShortcutType
  visibility: Visibility
  tags: string[]
  expires_at?: string | null
  owner_email?: string | null
}

export function createShortcut(input: ShortcutInput): Promise<Shortcut> {
  return api<Shortcut>('/api/v1/shortcuts', { method: 'POST', body: input })
}

export function updateShortcut(
  pattern: string,
  patch: Partial<Omit<ShortcutInput, 'pattern'>>,
): Promise<Shortcut> {
  return api<Shortcut>(`/api/v1/shortcuts/${encodeURIComponent(pattern)}`, {
    method: 'PATCH',
    body: patch,
  })
}

export interface ResolveDebug {
  outcome: string
  target?: string | null
}

/** What would `/{pattern}` do right now (local + upstream cache insight). */
export function debugResolve(pattern: string): Promise<ResolveDebug> {
  return api<ResolveDebug>(`/api/v1/resolve?pattern=${encodeURIComponent(pattern)}`)
}

/** Slug suggestion for an empty pattern, derived from the target URL. */
export function suggestPatternFor(target: string): string {
  let url: URL
  try {
    url = new URL(target.trim())
  } catch {
    return ''
  }
  const segments = url.pathname.split('/').filter((part) => part !== '')
  const raw = segments.length > 0 ? segments.slice(-2).join('-') : hostStem(url.hostname)
  if (raw === '') {
    return ''
  }
  return raw
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

/** Hostname minus www prefix and final TLD label (IPs get no suggestion). */
function hostStem(hostname: string): string {
  const host = hostname.toLowerCase().replace(/^www\./, '')
  const labels = host.split('.')
  if (labels.every((label) => /^\d+$/.test(label))) {
    return ''
  }
  if (labels.length < 2) {
    return host
  }
  return labels.slice(0, -1).join('-')
}

/** True when the target carries a dynamic placeholder. */
export function hasDynamicPlaceholder(target: string): boolean {
  return /\{[^}]+\}|\[arg\]/i.test(target)
}
