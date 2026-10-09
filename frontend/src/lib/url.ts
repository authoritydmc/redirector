/** URL helpers shared by redirect views (mirrors backend is_safe_redirect_target). */

export function isExternalUrl(target: string): boolean {
  try {
    const scheme = new URL(target).protocol.toLowerCase()
    return scheme === 'http:' || scheme === 'https:'
  } catch {
    return false
  }
}

export function joinPath(...parts: string[]): string {
  return parts
    .map((part, index) =>
      index === 0 ? part.replace(/\/+$/, '') : part.replace(/^\/+|\/+$/g, ''),
    )
    .filter((part) => part.length > 0)
    .join('/')
}
