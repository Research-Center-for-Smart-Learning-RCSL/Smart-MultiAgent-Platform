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
import { computed, readonly, ref, type ComputedRef, type Ref } from 'vue'

import { AuthError, NetworkError } from '@shared/errors'
import { markConnectionLost, markConnectionRestored } from '@shared/composables/useNetworkStatus'
import { i18n } from '@shared/i18n'
import { OpenAPI } from '@shared/api-client'

import type { ProblemJson } from './problem-json'
import { parseProblem } from './problem-json'

// The access token is held in a `ref` — not a plain `let` — so Vue `computed`s
// that decode it (impersonation banner, read-only gating) re-evaluate whenever
// `setAccessToken` runs. A non-reactive module variable would cache stale
// claims forever (FE-8).
const accessTokenRef = ref<string | null>(null)
// Guest sessions scope every request to one chatroom. Stored alongside the
// token so the guest refresh and ws-ticket endpoints know their path.
const guestChatroomIdRef = ref<string | null>(null)

// 'gone': the room itself no longer exists (its workspace or project was
// deleted), so neither rejoining nor re-enabling links can bring it back.
export type GuestSessionEnd = 'expired' | 'disabled' | 'gone'
// Recorded, not derived from the token: the token is gone by the time the
// session is known to have ended, and every consumer that has to explain the
// end (the room banner, the guard, hydrate) would otherwise forget the tab was
// ever a guest.
const guestSessionEndRef = ref<GuestSessionEnd | null>(null)
export const guestSessionEnd: Readonly<Ref<GuestSessionEnd | null>> = readonly(guestSessionEndRef)

const GUEST_DISABLED_TYPE = '/conversation/guest-access-disabled'
const ROOM_GONE_TYPE = '/conversation/chatroom-not-found'
const GUEST_TICKET_PATH = '/guest/ws-ticket'
const GUEST_SESSION_ENDED = {
  type: 'https://smap.local/problems/auth/token-expired',
  title: 'Guest session ended',
  status: 401,
}

let onUnauthorized: (() => void) | null = null
let refreshInFlight: Promise<boolean> | null = null

export function setAccessToken(token: string | null): void {
  accessTokenRef.value = token
}

export function getAccessToken(): string | null {
  return accessTokenRef.value
}

/**
 * The form of a room id the server uses for the guest cookie path. Cookie path
 * matching is case-sensitive, so a link carrying an upper-case id would never
 * present the cookie (F-22).
 */
export function canonicalRoomId(chatroomId: string): string {
  return chatroomId.toLowerCase()
}

export function setGuestContext(chatroomId: string): void {
  guestChatroomIdRef.value = canonicalRoomId(chatroomId)
  guestSessionEndRef.value = null
}

export function clearGuestContext(): void {
  guestChatroomIdRef.value = null
  guestSessionEndRef.value = null
}

export function getGuestChatroomId(): string | null {
  return guestChatroomIdRef.value
}

/** For the socket close codes, which reach the room rather than the transport. */
export function markGuestSessionEnded(reason: GuestSessionEnd): void {
  if (guestChatroomIdRef.value) guestSessionEndRef.value = reason
}

/**
 * What an answered guest refresh failure means. Only an answer that says the
 * session is gone ends it; a 5xx during a deploy, a 429 or a proxy's HTML page
 * is treated like no network (Q-6), or a valid 7-day cookie would be dropped
 * and, at boot, the hint holding the browser id deleted with it.
 */
function endReasonFor(status: number, problemType: unknown): GuestSessionEnd | null {
  const type = typeof problemType === 'string' ? problemType : ''
  if (status === 403 && type.endsWith(GUEST_DISABLED_TYPE)) return 'disabled'
  if (status === 404 && type.endsWith(ROOM_GONE_TYPE)) return 'gone'
  return status === 401 || status === 403 || status === 404 ? 'expired' : null
}

// Set by the app at boot. A tab whose guest restore found no network cannot
// tell whether the account refresh failed for the same reason, so before the
// guest refresh is retried the account gets its turn: it always wins (Q-1).
// Returns true when the account took over and the guest restore must stop.
let pendingGuestResolver: (() => Promise<boolean>) | null = null

export function onPendingGuestRestore(resolver: (() => Promise<boolean>) | null): void {
  pendingGuestResolver = resolver
}

/** A guest context with neither a token nor a recorded end: a restore not yet done. */
export function isGuestRestorePending(): boolean {
  return guestChatroomIdRef.value !== null && !accessTokenRef.value && !guestSessionEndRef.value
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

export const isGuestSession: ComputedRef<boolean> = computed(() => {
  const claims = accessTokenClaims.value
  return claims?.token_use === 'guest_access'
})

/**
 * The anonymous guest's session id (the guest token's `sub`), or null for any
 * other token. It is the id the server uses for the guest everywhere: presence
 * and typing frames, and the `sender_id` of the guest's messages.
 */
export const guestSessionId: ComputedRef<string | null> = computed(() => {
  if (!isGuestSession.value) return null
  const sub = accessTokenClaims.value?.sub
  return typeof sub === 'string' ? sub : null
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
  // Auth refresh rides on the httpOnly `smap_refresh` cookie; the client must
  // send credentials so the boot-time silent refresh (and any cross-origin
  // deployment) actually carries the cookie.
  withCredentials: true,
})

// --- Request interceptors (run in order) ---
// Named consts (not inline arrows) so the *same* function references can be
// registered on a second axios instance without duplicating the logic — see
// the bare-`axios`-singleton registration below.

// #1: Inject Authorization header
function injectAuthHeader(
  config: InternalAxiosRequestConfig,
): InternalAxiosRequestConfig {
  const token = accessTokenRef.value
  if (token && config.headers) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
}

// #2: Inject Idempotency-Key on POST when caller opts in
function injectIdempotencyKey(
  config: InternalAxiosRequestConfig,
): InternalAxiosRequestConfig {
  if (
    config.method === 'post' &&
    config.headers?.['X-Idempotent'] !== undefined
  ) {
    config.headers['Idempotency-Key'] = crypto.randomUUID()
    delete config.headers['X-Idempotent']
  }
  return config
}

// #3: Inject Accept-Language from i18n locale
function injectAcceptLanguage(
  config: InternalAxiosRequestConfig,
): InternalAxiosRequestConfig {
  if (config.headers) {
    config.headers['Accept-Language'] = i18n.global.locale.value
  }
  return config
}

http.interceptors.request.use(injectAuthHeader)
http.interceptors.request.use(injectIdempotencyKey)
http.interceptors.request.use(injectAcceptLanguage)

// --- Response interceptor ---

function handleResponseSuccess<T>(response: T): T {
  // Any answered request proves the connection is alive — clear an offline
  // banner the instant real traffic flows again (§4.4).
  markConnectionRestored()
  return response
}

async function handleResponseError(
  error: AxiosError<ProblemJson>,
  instance: AxiosInstance,
) {
  // Network error (no response at all): the server was unreachable. Flip the
  // global offline banner; its probe loop drives recovery.
  if (!error.response) {
    // A deliberately canceled/aborted request (e.g. an aborted query) is not
    // a connectivity failure — don't misread it as the connection dropping.
    if (axios.isCancel(error)) {
      throw error
    }
    markConnectionLost()
    throw new NetworkError(error.message || 'Network request failed')
  }
  // A *response* arrived (even an error one) — connectivity is fine.
  markConnectionRestored()

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
  // Only the socket ticket speaks for the held session; the landing page's
  // session-create can name another room and reports its own answer.
  if (guestChatroomIdRef.value && (original.url ?? '').endsWith(GUEST_TICKET_PATH)) {
    const end = endReasonFor(status, problemType)
    if (end === 'disabled' || end === 'gone') guestSessionEndRef.value = end
  }
  const isTokenRevoked = problemType.endsWith('/auth/token-revoked')
  const wasAuthenticated = Boolean(original.headers?.Authorization)
  const isRefreshEligible =
    status === 401 && !isTokenRevoked && wasAuthenticated

  if (isRefreshEligible && !original._retry) {
    original._retry = true
    const ok = await attemptRefresh()
    if (ok) return instance(original)
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
}

http.interceptors.response.use(handleResponseSuccess, (error: AxiosError<ProblemJson>) =>
  handleResponseError(error, http),
)

// The generated OpenAPI client (`shared/api-client`) has no way to inject a
// custom axios instance into its per-service static methods — they always
// call the bare default `axios` singleton (see core/request.ts's
// `axiosClient: AxiosInstance = axios` default). Registering the same three
// request interceptors here — not duplicating them, the exact same function
// references as `http`'s — gives every generated-client call the same
// bearer-token injection, idempotency keys, and Accept-Language as `http`.
// The response interceptor gives it the same silent 401-refresh-and-replay
// and problem+json → typed-error behavior for free.
//
// This makes the bare `axios` singleton a second *instrumented* instance,
// app-wide. Do not add a new bare `import axios from 'axios'` call anywhere
// else in the app expecting it to be uninstrumented — use `refreshHttp`
// below as the pattern for a deliberately bare call.
axios.interceptors.request.use(injectAuthHeader)
axios.interceptors.request.use(injectIdempotencyKey)
axios.interceptors.request.use(injectAcceptLanguage)
axios.interceptors.response.use(handleResponseSuccess, (error: AxiosError<ProblemJson>) =>
  handleResponseError(error, axios),
)

// The generated client's per-request `withCredentials` comes from this
// singleton config, not from the (uninstrumented-by-default) bare `axios`
// instance — mirrors `http`'s own `withCredentials: true` above. `TOKEN` and
// `HEADERS` stay unset deliberately: the interceptors just registered above
// already inject Authorization/Accept-Language after request.ts's own
// header-building step runs, so setting them again here would be redundant.
OpenAPI.WITH_CREDENTIALS = true

// Dedicated, deliberately uninstrumented instance for the refresh call
// itself: it must never carry a (possibly stale) `Authorization` header, and
// must not be intercepted by the 401-refresh handler above — a refresh
// failing with 401 must not try to refresh itself. Now that the bare `axios`
// singleton carries the same interceptors as `http` (see above), this call
// can no longer use it directly.
const refreshHttp: AxiosInstance = axios.create({ withCredentials: true })

const GUEST_TOKEN_INVALID_TYPE = '/conversation/guest-token-invalid'
const SIBLING_ROTATION_RETRY_MS = 500

function isGuestTokenInvalid(error: unknown): boolean {
  if (!axios.isAxiosError<ProblemJson>(error) || !error.response) return false
  const type = error.response.data?.type
  return error.response.status === 404 && typeof type === 'string' && type.endsWith(GUEST_TOKEN_INVALID_TYPE)
}

async function postRefresh(url: string, isGuest: boolean) {
  try {
    return await refreshHttp.post<{ access_token: string }>(url, {})
  } catch (error) {
    // Tabs share the guest cookie and rotation is single-use ([R13.06b]): when two
    // refresh at once the server rotates for one and refuses the other, and by
    // the time the refusal lands the browser holds the winner's new cookie. One
    // retry tells that apart from a cookie that is really dead; without it the
    // loser would record an end and, at boot, delete the hint every tab shares.
    if (!isGuest || !isGuestTokenInvalid(error)) throw error
    await new Promise((resolve) => setTimeout(resolve, SIBLING_ROTATION_RETRY_MS))
    return await refreshHttp.post<{ access_token: string }>(url, {})
  }
}

async function attemptRefresh(): Promise<boolean> {
  if (refreshInFlight) return refreshInFlight

  refreshInFlight = (async () => {
    const guestRoom = guestChatroomIdRef.value
    try {
      const url = guestRoom
        ? `/api/guest/${encodeURIComponent(guestRoom)}/refresh`
        : '/api/auth/refresh'
      const res = await postRefresh(url, guestRoom !== null)
      // A sign-in can replace the guest context while a guest refresh is in
      // flight; its answer then speaks for nothing this tab still holds.
      if (guestRoom && guestChatroomIdRef.value !== guestRoom) return false
      setAccessToken(res.data.access_token)
      return true
    } catch (error) {
      if (!guestRoom) {
        setAccessToken(null)
        return false
      }
      if (guestChatroomIdRef.value !== guestRoom) return false
      // Q-6: only an answer that says the session is gone ends it. Anything
      // else may leave the 7-day cookie good; keep everything and let the
      // existing schedulers retry.
      const response = axios.isAxiosError<ProblemJson>(error) ? error.response : undefined
      const end = response ? endReasonFor(response.status, response.data?.type) : null
      if (!end) return false
      setAccessToken(null)
      // The context stays so the room can still say why it ended.
      guestSessionEndRef.value = end
      return false
    } finally {
      refreshInFlight = null
    }
  })()
  return refreshInFlight
}

/**
 * Boot-time restore of a guest session for the room in the URL (Q-1). Keeps
 * the context on every failure so the room, not `/login`, explains the
 * outcome: `'ended'` carries its reason in `guestSessionEnd`; `'offline'`
 * carries none and the room retries.
 */
export async function resumeGuestSession(
  chatroomId: string,
): Promise<'resumed' | 'ended' | 'offline'> {
  setGuestContext(chatroomId)
  if (await attemptRefresh()) return 'resumed'
  return guestSessionEndRef.value ? 'ended' : 'offline'
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
  if (guestChatroomIdRef.value) {
    if (guestSessionEndRef.value) throw new AuthError(GUEST_SESSION_ENDED)
    // A guest whose boot restore found no network holds a context and no
    // token. A ticket request without a bearer is not refresh-eligible, so
    // restore first; failing here puts the retry on the socket's backoff.
    if (isGuestRestorePending()) {
      if (pendingGuestResolver && (await pendingGuestResolver())) {
        throw new NetworkError('The account took over from a pending guest restore')
      }
      if (!(await attemptRefresh())) throw new NetworkError('Guest session could not be restored')
    }
  }
  const url = guestChatroomIdRef.value ? GUEST_TICKET_PATH : '/auth/ws-ticket'
  const res = await http.post<{ ticket: string; expires_in: number }>(url)
  return res.data.ticket
}
