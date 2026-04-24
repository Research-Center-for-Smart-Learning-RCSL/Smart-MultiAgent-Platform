// Axios HTTP transport with the full interceptor chain per §24.12:
//   1. Inject Authorization: Bearer <access_token>
//   2. Inject Idempotency-Key on POST when caller opts in
//   3. Inject Accept-Language from i18n locale
//   4. On 401+token-expired: silent refresh + single replay
//   5. On 429: parse Retry-After → RateLimitError
//   6. On non-2xx with application/problem+json → typed ApiError subclass

import axios, {
  type AxiosError,
  type AxiosInstance,
  type InternalAxiosRequestConfig,
} from 'axios'

import { AuthError, NetworkError } from '@shared/errors'
import { i18n } from '@shared/i18n'

import type { ProblemJson } from './problem-json'
import { parseProblem } from './problem-json'

const REFRESH_STORAGE_KEY = 'smap:refresh_token'

let accessToken: string | null = null
let onUnauthorized: (() => void) | null = null
let refreshInFlight: Promise<boolean> | null = null

export function setAccessToken(token: string | null): void {
  accessToken = token
}

export function getAccessToken(): string | null {
  return accessToken
}

export function setRefreshToken(token: string | null): void {
  if (token) sessionStorage.setItem(REFRESH_STORAGE_KEY, token)
  else sessionStorage.removeItem(REFRESH_STORAGE_KEY)
}

export function getRefreshToken(): string | null {
  return sessionStorage.getItem(REFRESH_STORAGE_KEY)
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
  if (accessToken && config.headers) {
    config.headers.Authorization = `Bearer ${accessToken}`
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

    // #4: Silent refresh on 401 + token-expired
    const isTokenExpired =
      status === 401 && problem?.type?.endsWith('/auth/token-expired')

    if (isTokenExpired && !original._retry) {
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
  const refresh = getRefreshToken()
  if (!refresh) return false

  refreshInFlight = (async () => {
    try {
      const res = await axios.post<{
        access_token: string
        refresh_token: string
      }>('/api/auth/refresh', { refresh_token: refresh })
      setAccessToken(res.data.access_token)
      setRefreshToken(res.data.refresh_token)
      return true
    } catch {
      setAccessToken(null)
      setRefreshToken(null)
      return false
    } finally {
      refreshInFlight = null
    }
  })()
  return refreshInFlight
}
