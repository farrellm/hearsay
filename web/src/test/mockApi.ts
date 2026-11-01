import { vi } from 'vitest'

/** A route's answer: a JSON body, or a whole Response (an error, a 204). */
export type Handler = (init?: RequestInit) => unknown

/** Stand in for the server: `METHOD /path` → handler. Unknown routes are 404s. */
export function mockApi(overrides: Record<string, Handler> = {}) {
  const routes: Record<string, Handler> = {
    'GET /api/health': () => ({ status: 'ok', version: '0.1.0', schema: 2 }),
    'GET /api/feeds': () => [],
    ...overrides,
  }
  const fetchMock = vi.fn<typeof fetch>((input, init) => {
    const href = input instanceof Request ? input.url : input.toString()
    const url = new URL(href, 'http://localhost')
    const key = `${init?.method ?? 'GET'} ${decodeURIComponent(url.pathname)}`
    const handler = routes[key]
    if (!handler) return Promise.resolve(new Response(null, { status: 404 }))
    const answer = handler(init)
    return Promise.resolve(answer instanceof Response ? answer : Response.json(answer))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

/** A JSON error as the server sends one. */
export function apiError(status: number, detail: string): Response {
  return Response.json({ detail }, { status })
}

/** The body a mocked request was sent with. */
export function sentJson(fetchMock: ReturnType<typeof mockApi>, route: string): unknown {
  const [method, path] = route.split(' ')
  const call = fetchMock.mock.calls.findLast(
    ([input, init]) => (init?.method ?? 'GET') === method && input === path,
  )
  if (!call) throw new Error(`no ${route} request`)
  return JSON.parse(call[1]?.body as string)
}
