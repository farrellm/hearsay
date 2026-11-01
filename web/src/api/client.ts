// Typed client for the Hearsay server API (/api, design.md §10).

class ApiError extends Error {
  override readonly name = 'ApiError'
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(method: string, route: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method }
  if (body !== undefined) {
    init.headers = { 'Content-Type': 'application/json' }
    init.body = JSON.stringify(body)
  }
  const res = await fetch(`/api${route}`, init)
  if (!res.ok) {
    let message = `HTTP ${res.status}`
    try {
      const data = (await res.json()) as { detail?: unknown }
      if (typeof data.detail === 'string') message = data.detail
    } catch {
      // Not JSON (a proxy's error page): keep the status line.
    }
    throw new ApiError(res.status, message)
  }
  return (await res.json()) as T
}

export interface Health {
  status: 'ok'
  version: string
  schema: number
}

export const api = {
  health: () => request<Health>('GET', '/health'),
}
