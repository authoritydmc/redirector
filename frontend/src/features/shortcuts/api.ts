import { api } from '../../lib/client'
import type { components } from '../../lib/api'

export type Shortcut = components['schemas']['ShortcutRead']
export type ShortcutList = components['schemas']['ShortcutListResponse']
export type BulkDeleteResult = components['schemas']['BulkDeleteResponse']

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
