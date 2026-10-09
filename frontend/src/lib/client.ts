/** Typed fetch wrapper for the v3 API (auth header, JSON, problem errors). */

export class ApiError extends Error {
  status: number
  code?: string

  constructor(status: number, message: string, code?: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

interface RequestOptions {
  method?: string
  body?: unknown
  token?: string | null
}

interface ProblemBody {
  detail?: string
  title?: string
  code?: string
}

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const token = options.token ?? localStorage.getItem('redirector.token')
  const res = await fetch(path, {
    method: options.method ?? 'GET',
    headers: {
      'Content-Type': 'application/json',
      ...(token !== null && token !== '' ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  })
  if (res.status === 204) {
    return undefined as T
  }
  let data: ProblemBody & Record<string, unknown> = {}
  try {
    data = (await res.json()) as typeof data
  } catch {
    // Non-JSON body (proxies, outages): fall through to the generic error.
  }
  if (!res.ok) {
    const message =
      typeof data.detail === 'string'
        ? data.detail
        : typeof data.title === 'string'
          ? data.title
          : `Request failed (HTTP ${res.status})`
    throw new ApiError(res.status, message, data.code)
  }
  return data as T
}
