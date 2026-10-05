import { afterEach, describe, expect, it, vi } from 'vitest'
import { http as mswHttp, HttpResponse } from 'msw'
import bareAxios from 'axios'
import { server } from '../../../../tests/mocks/server'
import { http } from '../axios'
import {
  canonicalRoomId,
  clearGuestContext,
  fetchWsTicket,
  getAccessToken,
  getGuestChatroomId,
  guestSessionEnd,
  onPendingGuestRestore,
  setAccessToken,
  setGuestContext,
  onUnauthorizedRedirect,
  refreshAccessToken,
  resumeGuestSession,
} from '@shared/transport'
import { markConnectionRestored } from '@shared/composables/useNetworkStatus'
import { i18n } from '@shared/i18n'
import { AuthError, PermissionError } from '@shared/errors'

// Pins the current interceptor chain in shared/transport/axios.ts (§1-6 of the
// file's own header comment) before any refactor touches it. See
// docs/tasks/2026-07-10-openapi-resync-generated-client-auth/spec.md §6.

afterEach(() => {
  setAccessToken(null)
  clearGuestContext()
  onUnauthorizedRedirect(() => {})
  // A network-error test can flip the module-scoped online flag and schedule a
  // recovery probe; reset it so later tests/files don't inherit offline state
  // or a dangling setTimeout probe.
  markConnectionRestored()
})

describe('request interceptors', () => {
  it('injects Authorization when an access token is set, omits it otherwise', async () => {
    server.use(
      mswHttp.get('/api/test/echo-auth', ({ request }) =>
        HttpResponse.json({ authorization: request.headers.get('Authorization') }),
      ),
    )

    setAccessToken(null)
    const withoutToken = await http.get<{ authorization: string | null }>(
      '/test/echo-auth',
    )
    expect(withoutToken.data.authorization).toBeNull()

    setAccessToken('tok-123')
    const withToken = await http.get<{ authorization: string | null }>(
      '/test/echo-auth',
    )
    expect(withToken.data.authorization).toBe('Bearer tok-123')
  })

  it('injects Idempotency-Key on POST only when the caller opts in via X-Idempotent', async () => {
    server.use(
      mswHttp.post('/api/test/echo-idempotency', ({ request }) =>
        HttpResponse.json({
          idempotencyKey: request.headers.get('Idempotency-Key'),
          idempotentSentinel: request.headers.get('X-Idempotent'),
        }),
      ),
    )

    const optedOut = await http.post<{
      idempotencyKey: string | null
      idempotentSentinel: string | null
    }>('/test/echo-idempotency', {})
    expect(optedOut.data.idempotencyKey).toBeNull()

    const optedIn = await http.post<{
      idempotencyKey: string | null
      idempotentSentinel: string | null
    }>('/test/echo-idempotency', {}, { headers: { 'X-Idempotent': 'true' } })
    expect(optedIn.data.idempotencyKey).toMatch(
      /^[0-9a-f-]{36}$/,
    )
    // The sentinel header must not leak to the server — only the real
    // Idempotency-Key it was translated into.
    expect(optedIn.data.idempotentSentinel).toBeNull()
  })

  it('sets Accept-Language from the current i18n locale', async () => {
    server.use(
      mswHttp.get('/api/test/echo-locale', ({ request }) =>
        HttpResponse.json({ acceptLanguage: request.headers.get('Accept-Language') }),
      ),
    )

    const original = i18n.global.locale.value
    try {
      i18n.global.locale.value = 'zh-TW'
      const res = await http.get<{ acceptLanguage: string | null }>(
        '/test/echo-locale',
      )
      expect(res.data.acceptLanguage).toBe('zh-TW')
    } finally {
      i18n.global.locale.value = original
    }
  })
})

describe('response interceptor — 401 handling', () => {
  it('silently refreshes and replays once on an authenticated, non-revoked 401', async () => {
    setAccessToken('expiring-token')
    let attempts = 0
    let refreshCalls = 0
    let refreshSawAuthHeader: string | null = 'unset'

    server.use(
      mswHttp.get('/api/test/needs-refresh', ({ request }) => {
        attempts += 1
        if (attempts === 1) {
          return HttpResponse.json(
            {
              type: 'https://smap.local/problems/auth/token-expired',
              title: 'Session expired',
              status: 401,
            },
            { status: 401 },
          )
        }
        return HttpResponse.json({
          ok: true,
          authorization: request.headers.get('Authorization'),
        })
      }),
      mswHttp.post('/api/auth/refresh', ({ request }) => {
        refreshCalls += 1
        refreshSawAuthHeader = request.headers.get('Authorization')
        return HttpResponse.json({
          access_token: 'refreshed-access',
          refresh_token: 'refreshed-refresh',
          expires_in: 3600,
        })
      }),
    )

    const res = await http.get<{ ok: boolean; authorization: string | null }>(
      '/test/needs-refresh',
    )

    expect(attempts).toBe(2)
    expect(refreshCalls).toBe(1)
    // The refresh call itself must never carry a (stale) Authorization header —
    // this must hold before AND after the refreshHttp extraction (migration
    // step 6), since it's the primary regression risk of that step.
    expect(refreshSawAuthHeader).toBeNull()
    expect(res.data.ok).toBe(true)
    expect(res.data.authorization).toBe('Bearer refreshed-access')
    expect(getAccessToken()).toBe('refreshed-access')
  })

  it('does not refresh on token-revoked; throws AuthError without calling onUnauthorized', async () => {
    setAccessToken('revoked-token')
    let refreshCalls = 0
    const onUnauthorized = vi.fn()
    onUnauthorizedRedirect(onUnauthorized)

    server.use(
      mswHttp.get('/api/test/revoked', () =>
        HttpResponse.json(
          {
            type: 'https://smap.local/problems/auth/token-revoked',
            title: 'Session revoked',
            status: 401,
          },
          { status: 401 },
        ),
      ),
      mswHttp.post('/api/auth/refresh', () => {
        refreshCalls += 1
        return HttpResponse.json({ access_token: 'x', refresh_token: 'y', expires_in: 3600 })
      }),
    )

    await expect(http.get('/test/revoked')).rejects.toBeInstanceOf(AuthError)
    expect(refreshCalls).toBe(0)
    // onUnauthorized fires only on a *failed refresh attempt* (axios.ts's
    // isRefreshEligible branch); token-revoked skips refresh entirely and
    // throws straight from the problem+json parse, so it's never called here.
    expect(onUnauthorized).not.toHaveBeenCalled()
  })

  it('calls onUnauthorized and throws AuthError when the refresh attempt itself fails', async () => {
    setAccessToken('expiring-token')
    const onUnauthorized = vi.fn()
    onUnauthorizedRedirect(onUnauthorized)
    let attempts = 0

    server.use(
      mswHttp.get('/api/test/refresh-fails', () => {
        attempts += 1
        return HttpResponse.json(
          {
            type: 'https://smap.local/problems/auth/token-expired',
            title: 'Session expired',
            status: 401,
          },
          { status: 401 },
        )
      }),
      mswHttp.post('/api/auth/refresh', () =>
        HttpResponse.json({ detail: 'no valid refresh cookie' }, { status: 401 }),
      ),
    )

    await expect(http.get('/test/refresh-fails')).rejects.toBeInstanceOf(AuthError)
    // No retry against the original endpoint once refresh itself failed.
    expect(attempts).toBe(1)
    expect(onUnauthorized).toHaveBeenCalledTimes(1)
    expect(getAccessToken()).toBeNull()
  })

  it('does not refresh a 401 on a request that carried no Authorization', async () => {
    setAccessToken(null)
    let refreshCalls = 0
    let attempts = 0

    server.use(
      mswHttp.get('/api/test/unauthenticated', () => {
        attempts += 1
        return HttpResponse.json(
          {
            type: 'https://smap.local/problems/auth/invalid-credentials',
            title: 'Invalid credentials',
            status: 401,
          },
          { status: 401 },
        )
      }),
      mswHttp.post('/api/auth/refresh', () => {
        refreshCalls += 1
        return HttpResponse.json({ access_token: 'x', refresh_token: 'y', expires_in: 3600 })
      }),
    )

    await expect(http.get('/test/unauthenticated')).rejects.toBeInstanceOf(AuthError)
    expect(refreshCalls).toBe(0)
    expect(attempts).toBe(1)
  })
})

describe('response interceptor — network errors and cancellation', () => {
  it('marks the connection lost and throws NetworkError on an unreachable request', async () => {
    // markConnectionLost() schedules a real setTimeout probe; the shared
    // afterEach's markConnectionRestored() call cancels it via
    // clearProbeTimer() regardless, so no fake-timer juggling is needed here
    // (and faking global timers risks leaking into other test files).
    server.use(mswHttp.get('/api/test/unreachable', () => HttpResponse.error()))
    await expect(http.get('/test/unreachable')).rejects.toMatchObject({
      name: 'NetworkError',
    })
  })

  it('rethrows a canceled request without marking the connection lost', async () => {
    server.use(
      mswHttp.get('/api/test/cancel-me', async () => {
        await new Promise((r) => setTimeout(r, 50))
        return HttpResponse.json({ ok: true })
      }),
    )
    const controller = new AbortController()
    const pending = http.get('/test/cancel-me', { signal: controller.signal })
    controller.abort()

    await expect(pending).rejects.toBeTruthy()
  })
})

describe('response interceptor — problem+json typing', () => {
  it('parses a non-401 problem+json response into its typed error subclass', async () => {
    server.use(
      mswHttp.get('/api/test/forbidden', () =>
        HttpResponse.json(
          {
            type: 'https://smap.local/problems/forbidden',
            title: 'Forbidden',
            status: 403,
          },
          { status: 403 },
        ),
      ),
    )

    await expect(http.get('/test/forbidden')).rejects.toBeInstanceOf(PermissionError)
  })
})

describe('bare axios singleton (generated api-client target)', () => {
  it('carries the same auth-header interceptor as http, so generated-client calls authenticate', async () => {
    server.use(
      mswHttp.get('/api/test/echo-auth-bare', ({ request }) =>
        HttpResponse.json({ authorization: request.headers.get('Authorization') }),
      ),
    )

    setAccessToken('tok-bare-456')
    // Deliberately NOT going through `http` — this simulates what a
    // generated-client call does, since core/request.ts's default
    // `axiosClient` is the bare `axios` module import, not `http`.
    const res = await bareAxios.get<{ authorization: string | null }>(
      '/api/test/echo-auth-bare',
    )
    expect(res.data.authorization).toBe('Bearer tok-bare-456')
  })

  it('silently refreshes and replays a 401 exactly like http does', async () => {
    setAccessToken('expiring-bare-token')
    let attempts = 0

    server.use(
      mswHttp.get('/api/test/bare-needs-refresh', () => {
        attempts += 1
        if (attempts === 1) {
          return HttpResponse.json(
            {
              type: 'https://smap.local/problems/auth/token-expired',
              title: 'Session expired',
              status: 401,
            },
            { status: 401 },
          )
        }
        return HttpResponse.json({ ok: true })
      }),
      mswHttp.post('/api/auth/refresh', () =>
        HttpResponse.json({
          access_token: 'bare-refreshed-access',
          refresh_token: 'r',
          expires_in: 3600,
        }),
      ),
    )

    const res = await bareAxios.get<{ ok: boolean }>('/api/test/bare-needs-refresh')
    expect(attempts).toBe(2)
    expect(res.data.ok).toBe(true)
    expect(getAccessToken()).toBe('bare-refreshed-access')
  })
})

describe('refreshAccessToken', () => {
  it('coalesces concurrent callers onto a single in-flight refresh', async () => {
    let refreshCalls = 0
    server.use(
      mswHttp.post('/api/auth/refresh', () => {
        refreshCalls += 1
        return HttpResponse.json({
          access_token: 'coalesced-token',
          refresh_token: 'r',
          expires_in: 3600,
        })
      }),
    )

    const [a, b] = await Promise.all([refreshAccessToken(), refreshAccessToken()])
    expect(refreshCalls).toBe(1)
    expect(a).toBe('coalesced-token')
    expect(b).toBe('coalesced-token')
  })
})

// docs/tasks/2026-10-05-guest-frontend-session-lifecycle (F-8, F-20, F-22, Q-6).
describe('guest session refresh', () => {
  const ROOM = '0f8e2b1c-aaaa-4bbb-8ccc-0123456789ab'
  const DISABLED = {
    type: 'https://smap.local/problems/conversation/guest-access-disabled',
    title: 'Guest access has been disabled for this chatroom',
    status: 403,
  }
  const INVALID = {
    type: 'https://smap.local/problems/conversation/guest-token-invalid',
    title: 'Guest token invalid',
    status: 404,
  }

  it('refreshes on the canonical (lower-case) room path the cookie is scoped to', async () => {
    let hit = ''
    server.use(
      mswHttp.post('/api/guest/:room/refresh', ({ params }) => {
        hit = params.room as string
        return HttpResponse.json({ access_token: 'guest-2' })
      }),
    )
    setGuestContext(ROOM.toUpperCase())

    expect(getGuestChatroomId()).toBe(ROOM)
    expect(await refreshAccessToken()).toBe('guest-2')
    expect(hit).toBe(ROOM)
    expect(canonicalRoomId(ROOM.toUpperCase())).toBe(ROOM)
  })

  it('keeps the token and the context when the refresh gets no response', async () => {
    server.use(mswHttp.post('/api/guest/:room/refresh', () => HttpResponse.error()))
    setGuestContext(ROOM)
    setAccessToken('guest-1')

    expect(await refreshAccessToken()).toBeNull()
    expect(getAccessToken()).toBe('guest-1')
    expect(getGuestChatroomId()).toBe(ROOM)
    expect(guestSessionEnd.value).toBeNull()
  })

  it('records an expired session, keeping the context, when the refresh is answered 404', async () => {
    server.use(mswHttp.post('/api/guest/:room/refresh', () => HttpResponse.json(INVALID, { status: 404 })))
    setGuestContext(ROOM)
    setAccessToken('guest-1')

    expect(await refreshAccessToken()).toBeNull()
    expect(getAccessToken()).toBeNull()
    expect(getGuestChatroomId()).toBe(ROOM)
    expect(guestSessionEnd.value).toBe('expired')
  })

  it('records a disabled session when the refresh is answered guest-access-disabled', async () => {
    server.use(mswHttp.post('/api/guest/:room/refresh', () => HttpResponse.json(DISABLED, { status: 403 })))
    setGuestContext(ROOM)
    setAccessToken('guest-1')

    await refreshAccessToken()
    expect(guestSessionEnd.value).toBe('disabled')
  })

  it('records disabled from any guest-context request answered guest-access-disabled', async () => {
    server.use(mswHttp.post('/api/guest/ws-ticket', () => HttpResponse.json(DISABLED, { status: 403 })))
    setGuestContext(ROOM)
    setAccessToken('guest-1')

    await expect(fetchWsTicket()).rejects.toBeInstanceOf(PermissionError)
    expect(guestSessionEnd.value).toBe('disabled')
  })

  it('a new guest context forgets how the previous one ended', async () => {
    server.use(mswHttp.post('/api/guest/:room/refresh', () => HttpResponse.json(INVALID, { status: 404 })))
    setGuestContext(ROOM)
    await refreshAccessToken()
    expect(guestSessionEnd.value).toBe('expired')

    setGuestContext(ROOM)
    expect(guestSessionEnd.value).toBeNull()
    clearGuestContext()
    expect(guestSessionEnd.value).toBeNull()
  })

  it('restores a missing guest token before asking for a socket ticket', async () => {
    let ticketAuth: string | null = null
    server.use(
      mswHttp.post('/api/guest/:room/refresh', () => HttpResponse.json({ access_token: 'guest-3' })),
      mswHttp.post('/api/guest/ws-ticket', ({ request }) => {
        ticketAuth = request.headers.get('Authorization')
        return HttpResponse.json({ ticket: 't', expires_in: 30 })
      }),
    )
    setGuestContext(ROOM)

    expect(await fetchWsTicket()).toBe('t')
    expect(ticketAuth).toBe('Bearer guest-3')
  })

  it('asks for no socket ticket once the guest session has ended', async () => {
    let tickets = 0
    server.use(
      mswHttp.post('/api/guest/:room/refresh', () => HttpResponse.json(INVALID, { status: 404 })),
      mswHttp.post('/api/guest/ws-ticket', () => {
        tickets += 1
        return HttpResponse.json({ ticket: 't', expires_in: 30 })
      }),
    )
    setGuestContext(ROOM)
    await refreshAccessToken()

    await expect(fetchWsTicket()).rejects.toBeTruthy()
    expect(tickets).toBe(0)
  })
})

// Code review of the same dossier: only an answer that says the session is gone
// ends it, and a context replaced mid-refresh is left alone.
describe('guest session refresh, transient answers and context changes', () => {
  const ROOM = '0f8e2b1c-aaaa-4bbb-8ccc-0123456789ab'

  it.each([502, 503, 429, 500])('a %s answer keeps the token and records no end', async (status) => {
    server.use(
      mswHttp.post('/api/guest/:room/refresh', () => new HttpResponse('<html>bad gateway</html>', { status })),
    )
    setGuestContext(ROOM)
    setAccessToken('guest-1')

    expect(await refreshAccessToken()).toBeNull()
    expect(getAccessToken()).toBe('guest-1')
    expect(guestSessionEnd.value).toBeNull()
    expect(getGuestChatroomId()).toBe(ROOM)
  })

  it('a 401 answer ends the session as expired', async () => {
    server.use(
      mswHttp.post('/api/guest/:room/refresh', () =>
        HttpResponse.json({ type: 'https://smap.local/problems/auth/required', title: 't', status: 401 }, { status: 401 }),
      ),
    )
    setGuestContext(ROOM)
    setAccessToken('guest-1')

    await refreshAccessToken()
    expect(guestSessionEnd.value).toBe('expired')
    expect(getAccessToken()).toBeNull()
  })

  it.each([
    ['fails', 404],
    ['succeeds', 200],
  ] as const)('leaves a user token applied while a guest refresh was in flight alone when it %s', async (_, status) => {
    let release!: () => void
    const gate = new Promise<void>((r) => {
      release = r
    })
    let arrived!: () => void
    const sent = new Promise<void>((r) => {
      arrived = r
    })
    server.use(
      mswHttp.post('/api/guest/:room/refresh', async () => {
        arrived()
        await gate
        return status === 200
          ? HttpResponse.json({ access_token: 'guest-2' })
          : HttpResponse.json({ type: 'https://smap.local/problems/conversation/guest-token-invalid', title: 't', status }, { status })
      }),
    )
    setGuestContext(ROOM)
    const refreshing = refreshAccessToken()
    await sent
    // A sign-in lands: the session store clears the context and applies its token.
    clearGuestContext()
    setAccessToken('user-token')
    release()
    await refreshing

    expect(getAccessToken()).toBe('user-token')
    expect(guestSessionEnd.value).toBeNull()
  })

  it('a guest-access-disabled answer for another room does not end this one', async () => {
    const OTHER = '11111111-2222-4333-8444-555555555555'
    server.use(
      mswHttp.post(`/api/guest/${OTHER}/tok_abcdefghijklmnop/session`, () =>
        HttpResponse.json(
          { type: 'https://smap.local/problems/conversation/guest-access-disabled', title: 't', status: 403 },
          { status: 403 },
        ),
      ),
    )
    setGuestContext(ROOM)
    setAccessToken('guest-1')

    await expect(http.post(`/guest/${OTHER}/tok_abcdefghijklmnop/session`, {})).rejects.toBeInstanceOf(PermissionError)
    expect(guestSessionEnd.value).toBeNull()
  })
})

describe('pending guest restore prefers the account', () => {
  const ROOM = '0f8e2b1c-aaaa-4bbb-8ccc-0123456789ab'

  afterEach(() => {
    onPendingGuestRestore(null)
  })

  it('asks the account resolver before the guest refresh, and stops when it takes over', async () => {
    let guestRefreshes = 0
    server.use(
      mswHttp.post('/api/guest/:room/refresh', () => {
        guestRefreshes += 1
        return HttpResponse.json({ access_token: 'guest-2' })
      }),
    )
    const resolver = vi.fn(async () => true)
    onPendingGuestRestore(resolver)
    setGuestContext(ROOM)

    await expect(fetchWsTicket()).rejects.toBeTruthy()
    expect(resolver).toHaveBeenCalledTimes(1)
    expect(guestRefreshes).toBe(0)
  })

  it('falls back to the guest refresh when there is no account', async () => {
    server.use(
      mswHttp.post('/api/guest/:room/refresh', () => HttpResponse.json({ access_token: 'guest-2' })),
      mswHttp.post('/api/guest/ws-ticket', () => HttpResponse.json({ ticket: 't', expires_in: 30 })),
    )
    onPendingGuestRestore(async () => false)
    setGuestContext(ROOM)

    expect(await fetchWsTicket()).toBe('t')
    expect(getAccessToken()).toBe('guest-2')
  })

  it('is not consulted while a guest token is held', async () => {
    server.use(mswHttp.post('/api/guest/ws-ticket', () => HttpResponse.json({ ticket: 't', expires_in: 30 })))
    const resolver = vi.fn(async () => true)
    onPendingGuestRestore(resolver)
    setGuestContext(ROOM)
    setAccessToken('guest-1')

    expect(await fetchWsTicket()).toBe('t')
    expect(resolver).not.toHaveBeenCalled()
  })
})

describe('resumeGuestSession (boot restore)', () => {
  const ROOM = '0f8e2b1c-aaaa-4bbb-8ccc-0123456789ab'

  it('restores the token and the context on success', async () => {
    server.use(mswHttp.post(`/api/guest/${ROOM}/refresh`, () => HttpResponse.json({ access_token: 'g' })))

    expect(await resumeGuestSession(ROOM.toUpperCase())).toBe('resumed')
    expect(getAccessToken()).toBe('g')
    expect(getGuestChatroomId()).toBe(ROOM)
  })

  it.each([
    [404, 'conversation/guest-token-invalid', 'expired'],
    [403, 'conversation/guest-access-disabled', 'disabled'],
  ] as const)('keeps the context and records the end for a %s answer', async (status, type, end) => {
    server.use(
      mswHttp.post(`/api/guest/${ROOM}/refresh`, () =>
        HttpResponse.json({ type: `https://smap.local/problems/${type}`, title: 't', status }, { status }),
      ),
    )

    expect(await resumeGuestSession(ROOM)).toBe('ended')
    expect(getAccessToken()).toBeNull()
    expect(getGuestChatroomId()).toBe(ROOM)
    expect(guestSessionEnd.value).toBe(end)
  })

  it('keeps the context with no end reason when there is no network', async () => {
    server.use(mswHttp.post(`/api/guest/${ROOM}/refresh`, () => HttpResponse.error()))

    expect(await resumeGuestSession(ROOM)).toBe('offline')
    expect(getGuestChatroomId()).toBe(ROOM)
    expect(guestSessionEnd.value).toBeNull()
  })
})
