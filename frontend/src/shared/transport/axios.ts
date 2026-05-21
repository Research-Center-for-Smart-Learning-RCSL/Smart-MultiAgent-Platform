// Axios HTTP transport with the full interceptor chain per §24.12:
//   1. Inject Authorization: Bearer <access_token>
//   2. Inject Idempotency-Key on POST when caller opts in
//   3. Inject Accept-Language from i18n locale
//   4. On any authenticated 401 (bar token-revoked): silent refresh + replay
//   5. On 429: parse Retry-After → RateLimitError
//   6. On non-2xx with application/problem+json → typed ApiError subclass

import axios, {
  type AxiosError,
  type AxiosInstance,
  type InternalAxiosRequestConfig,
} from 'axios'
import { computed, ref, type ComputedRef } from 'vue'

import { AuthError, NetworkError } from '@shared/errors'
import { i18n } from '@shared/i18n'

import type { ProblemJson } from './problem-json'
import { parseProblem } from './problem-json'

// The access token is held in a `ref` — not a plain `let` — so Vue `computed`s
// that decode it (impersonation banner, read-only gating) re-evaluate whenever
// `setAccessToken` runs. A non-reactive module variable would cache stale
// claims forever (FE-8).
const accessTokenRef = ref<string | null>(null)
let onUnauthorized: (() => void) | null = null
let refreshInFlight: Promise<boolean> | null = null

export function setAccessToken(token: string | null): void {
  accessTokenRef.value = token
}

export function getAccessToken(): string | null {
  return accessTokenRef.value
}

/**
 * Decode a JWT's payload claims. Handles base64url (the `-`/`_` alphabet),
 * which plain `atob` rejects. Returns `null` for a malformed token.
 */
export function decodeJwtClaims(token: string): Record<string, unknown> | null {
  try {
    const payload = token.split('.')[1]
    if (!payload) return null
    const base64 = payload.replace(/-/g, '+').replace(/_/g, '/')
    return JSON.parse(atob(base64)) as Record<string, unknown>
  } catch {
    return null
  }
}

/**
 * Reactive decoded claims of the current access token. Recomputes on every
 * `setAccessToken`, so any `computed` derived from it (e.g. `impersonated_by`)
 * stays live.
 */
export const accessTokenClaims: ComputedRef<Record<string, unknown> | null> =
  computed(() => {
    const token = accessTokenRef.value
    return token ? decodeJwtClaims(token) : null
  })

// Refresh token is managed exclusively via the httpOnly `smap_refresh` cookie
// set by the server. These stubs exist so callers need no changes.
export function setRefreshToken(_token: string | null): void {}

export function getRefreshToken(): string | null {
  return null
}

export function onUnauthorizedRedirect(cb: () => void): void {
  onUnauthorized = cb
}

export const http: AxiosInstance = axios.create({
  baseURL: '/api',
  headers: { 'Content-Type': 'application/json' },
})

// --- Request interceptors (run in order) ---

// #1: Inject Authorization header
http.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = accessTokenRef.value
  if (token && config.headers) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// #2: Inject Idempotency-Key on POST when caller opts in
http.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  if (
    config.method === 'post' &&
    config.headers?.['X-Idempotent'] !== undefined
  ) {
    config.headers['Idempotency-Key'] = crypto.randomUUID()
    delete config.headers['X-Idempotent']
  }
  return config
})

// #3: Inject Accept-Language from i18n locale
http.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  if (config.headers) {
    config.headers['Accept-Language'] = i18n.global.locale.value
  }
  return config
})

// --- Response interceptor ---
http.interceptors.response.use(
  (r) => r,
  async (error: AxiosError<ProblemJson>) => {
    // Network error (no response at all)
    if (!error.response) {
      throw new NetworkError(error.message || 'Network request failed')
    }

    const original = error.config as InternalAxiosRequestConfig & {
      _retry?: boolean
    }
    const problem = error.response.data
    const status = error.response.status

    // #4: Silent refresh. Any *authenticated* 401 is refresh-eligible — a
    // garbled or missing problem body, or an expiry signalled with some type
    // other than `token-expired`, must not bounce a user a refresh could have
    // saved (FE-9). Two exclusions:
    //   - `token-revoked`: the session is deliberately dead; a refresh of a
    //     killed family cannot help, so skip straight to logout.
    //   - requests that carried no `Authorization` header (login, the boot
    //     refresh): a 401 there is a credential failure, not an expiry —
    //     refreshing and replaying would mask the real error.
    const problemType = typeof problem?.type === 'string' ? problem.type : ''
    const isTokenRevoked = problemType.endsWith('/auth/token-revoked')
    const wasAuthenticated = Boolean(original.headers?.Authorization)
    const isRefreshEligible =
      status === 401 && !isTokenRevoked && wasAuthenticated

    if (isRefreshEligible && !original._retry) {
      original._retry = true
      const ok = await attemptRefresh()
      if (ok) return http(original)
      onUnauthorized?.()
      throw new AuthError(
        problem ?? {
          type: 'https://smap.local/problems/auth/token-expired',
          title: 'Session expired',
          status: 401,
        },
      )
    }

    // #5 + #6: Parse problem+json into typed error subclass
    if (problem && typeof problem.type === 'string') {
      const retryAfter = error.response.headers?.['retry-after'] as
        | string
        | null
      throw parseProblem(problem, retryAfter)
    }

    // Fallback for non-problem responses
    throw error
  },
)

async function attemptRefresh(): Promise<boolean> {
  if (refreshInFlight) return refreshInFlight

  refreshInFlight = (async () => {
    try {
      // No token in body — the browser sends the httpOnly smap_refresh cookie automatically.
      const res = await axios.post<{ access_token: string }>('/api/auth/refresh', {})
      setAccessToken(res.data.access_token)
      return true
    } catch {
      setAccessToken(null)
      return false
    } finally {
      refreshInFlight = null
    }
  })()
  return refreshInFlight
}

/**
 * Force an HTTP refresh and return the fresh access token (or `null` on
 * failure). Concurrent callers coalesce onto a single in-flight refresh.
 * The WS manager calls this before resending a token over the socket so a
 * long-backgrounded tab never presents an already-expired JWT (FE-7).
 */
export async function refreshAccessToken(): Promise<string | null> {
  const ok = await attemptRefresh()
  return ok ? getAccessToken() : null
}

/**
 * Fetch a short-lived, single-use WebSocket handshake ticket (FE-7).
 *
 * The JWT must never ride in `Sec-WebSocket-Protocol` — proxies and access
 * logs record that header. Instead this opaque ticket is fetched over HTTPS
 * (where the bearer token sits in `Authorization`, which infra redacts) and
 * redeemed once by the WS handshake. Because the request goes through `http`,
 * an expired access token is silently refreshed before the ticket is minted.
 */
export async function fetchWsTicket(): Promise<string> {
  const res = await http.post<{ ticket: string; expires_in: number }>(
    '/auth/ws-ticket',
  )
  return res.data.ticket
}
