import { api } from './client'

export interface SitePolicy {
  public_actions: string[]
  known_actions: string[]
}

/** Which mutating actions are open to anonymous clients (admin-curated). */
export function getSitePolicy(): Promise<SitePolicy> {
  return api<SitePolicy>('/api/v1/site/policy')
}
