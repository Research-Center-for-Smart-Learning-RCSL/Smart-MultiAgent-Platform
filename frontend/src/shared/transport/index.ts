// Axios HTTP transport with RFC 7807 error shaping and silent refresh.
//
// - Access token held in memory; refresh token in sessionStorage (R24.11).
// - On 401 with `type=…/problems/auth/token-expired`, attempt a single silent
//   refresh (R24.12 #4); replay the original request once, then give up.

import axios, {
  type AxiosError,
  type AxiosInstance,
  type InternalAxiosRequestConfig,
} from 'axios'

export interface ProblemJson {
  type: string
  title: string
  status: number
  detail?: string
  instance?: string
  [k: string]: unknown
}

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

http.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  if (accessToken && config.headers) {
    config.headers.Authorization = `Bearer ${accessToken}`
  }
  return config
})

http.interceptors.response.use(
  (r) => r,
  async (error: AxiosError<ProblemJson>) => {
    const original = error.config as InternalAxiosRequestConfig & { _retry?: boolean }
    const problem = error.response?.data
    const isTokenExpired =
      error.response?.status === 401 &&
      problem?.type?.endsWith('/auth/token-expired')

    if (isTokenExpired && !original._retry) {
      original._retry = true
      const ok = await attemptRefresh()
      if (ok) return http(original)
      onUnauthorized?.()
    }
    return Promise.reject(error)
  },
)

async function attemptRefresh(): Promise<boolean> {
  if (refreshInFlight) return refreshInFlight
  const refresh = getRefreshToken()
  if (!refresh) return false

  refreshInFlight = (async () => {
    try {
      const res = await axios.post<{ access_token: string; refresh_token: string }>(
        '/api/auth/refresh',
        { refresh_token: refresh },
      )
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

export function isProblemWithType(err: unknown, suffix: string): boolean {
  const ax = err as AxiosError<ProblemJson>
  return !!ax?.response?.data?.type?.endsWith(suffix)
}
