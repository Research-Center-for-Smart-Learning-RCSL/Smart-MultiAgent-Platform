import { http as mswHttp, HttpResponse } from 'msw'

// Shared scaffold for request-level api characterization specs: it records the
// outbound request (verb / path / query / If-Match / body) of the last matched
// route so a test can assert the wire contract. Each api-slice spec builds its own
// route list with `on(...)` and reads the captured request off `cap.value`.

export interface CapturedRequest {
  method: string
  path: string
  query: Record<string, string>
  ifMatch: string | null
  body: unknown
}

type CaptureMethod = 'get' | 'post' | 'patch' | 'put' | 'delete'

export function createRequestCapture(): {
  cap: { value: CapturedRequest | null }
  on: (method: CaptureMethod, path: string, json: unknown, status?: number) => ReturnType<typeof mswHttp.get>
} {
  const cap: { value: CapturedRequest | null } = { value: null }
  const record = async (request: Request): Promise<void> => {
    const url = new URL(request.url)
    let body: unknown = undefined
    if (request.method !== 'GET' && request.method !== 'DELETE') {
      body = await request.clone().json().catch(() => undefined)
    }
    cap.value = {
      method: request.method,
      path: url.pathname,
      query: Object.fromEntries(url.searchParams),
      ifMatch: request.headers.get('if-match'),
      body,
    }
  }
  const on = (method: CaptureMethod, path: string, json: unknown, status = 200) =>
    mswHttp[method](path, async ({ request }) => {
      await record(request)
      return json === null ? new HttpResponse(null, { status }) : HttpResponse.json(json, { status })
    })
  return { cap, on }
}
