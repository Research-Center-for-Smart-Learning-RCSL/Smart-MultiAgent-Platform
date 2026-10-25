import type { Redactor } from './log.ts'
import { retryAfterSec } from './time.ts'

export class HttpError extends Error {
  readonly status: number
  readonly body: string

  constructor(status: number, method: string, path: string, body: string) {
    super(`${method} ${path} -> ${status}: ${body.slice(0, 300)}`)
    this.status = status
    this.body = body
  }
}

export interface LoginResult {
  accessToken: string
}

/**
 * A Bearer-token API client for the operator's own work (setup, rate limits, evidence).
 * Participants never use it: they act through their browsers. A 401 triggers one
 * re-login, since the access token lives 15 minutes (`settings.py:154`); a 429 waits for
 * Retry-After.
 */
export class ApiClient {
  private readonly baseUrl: string
  private readonly redactor: Redactor
  private token: string | null = null
  private credentials: { email: string; password: string } | null = null
  private readonly fetchImpl: typeof fetch

  constructor(baseUrl: string, redactor: Redactor, fetchImpl: typeof fetch = fetch) {
    this.baseUrl = baseUrl.replace(/\/$/, '')
    this.redactor = redactor
    this.fetchImpl = fetchImpl
  }

  async login(email: string, password: string): Promise<void> {
    this.redactor.add(password)
    this.credentials = { email, password }
    await this.doLogin()
  }

  private async doLogin(): Promise<void> {
    if (!this.credentials) throw new Error('no credentials to log in with')
    const body = await this.raw('POST', '/api/auth/login', this.credentials, false)
    const { access_token: token } = body as { access_token: string }
    this.redactor.add(token)
    this.token = token
  }

  get<T>(path: string): Promise<T> {
    return this.request<T>('GET', path)
  }

  post<T>(path: string, body?: unknown, headers?: Record<string, string>): Promise<T> {
    return this.request<T>('POST', path, body, headers)
  }

  put<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>('PUT', path, body)
  }

  patch<T>(path: string, body?: unknown, headers?: Record<string, string>): Promise<T> {
    return this.request<T>('PATCH', path, body, headers)
  }

  /** Unauthenticated call, for the activation endpoints that take a token in the body. */
  anonymous<T>(method: string, path: string, body?: unknown): Promise<T> {
    return this.raw(method, path, body, false) as Promise<T>
  }

  private async request<T>(method: string, path: string, body?: unknown, headers?: Record<string, string>): Promise<T> {
    try {
      return (await this.raw(method, path, body, true, headers)) as T
    } catch (err) {
      if (err instanceof HttpError && err.status === 401 && this.credentials) {
        await this.doLogin()
        return (await this.raw(method, path, body, true, headers)) as T
      }
      throw err
    }
  }

  private async raw(
    method: string,
    path: string,
    body: unknown,
    auth: boolean,
    headers: Record<string, string> = {},
  ): Promise<unknown> {
    for (let attempt = 0; attempt < 5; attempt++) {
      const h: Record<string, string> = { Accept: 'application/json', ...headers }
      if (body !== undefined) h['Content-Type'] = 'application/json'
      if (auth && this.token) h.Authorization = `Bearer ${this.token}`
      // A redirect is refused: fetch would re-send the body (a password, on login) to a
      // host the allowedHost guard never checked.
      const init: RequestInit = { method, headers: h, redirect: 'error' }
      if (body !== undefined) init.body = JSON.stringify(body)
      const resp = await this.fetchImpl(this.baseUrl + path, init)
      // The edge nginx's limit_req on /api/auth/ answers 503 rather than 429. A 503 elsewhere
      // is the backend's own and is not retried.
      if (resp.status === 429 || (resp.status === 503 && path.startsWith('/api/auth/'))) {
        await new Promise((r) => setTimeout(r, retryAfterSec(resp.headers.get('retry-after'), 5) * 1000 + 250))
        continue
      }
      const text = await resp.text()
      if (!resp.ok) throw new HttpError(resp.status, method, path, this.redactor.redact(text))
      return text ? JSON.parse(text) : null
    }
    throw new HttpError(429, method, path, 'still rate limited after 5 attempts')
  }
}

/** The token carried in an activation or invite link (`#token=...` or `?token=...`), registered for redaction. */
export function tokenFromLink(url: string, redactor: Redactor): string {
  const u = new URL(url, 'http://placeholder.invalid')
  const fromHash = new URLSearchParams(u.hash.replace(/^#/, '')).get('token')
  const token = fromHash ?? u.searchParams.get('token')
  if (!token) throw new Error('link carries no token')
  redactor.add(token)
  return token
}
