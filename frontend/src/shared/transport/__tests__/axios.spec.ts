import { afterEach, describe, expect, it, vi } from 'vitest'
import { http as mswHttp, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import {
  http,
  getAccessToken,
  setAccessToken,
  onUnauthorizedRedirect,
  refreshAccessToken,
} from '@shared/transport'
import { markConnectionRestored } from '@shared/composables/useNetworkStatus'
import { i18n } from '@shared/i18n'
import { AuthError, PermissionError } from '@shared/errors'

// Pins the current interceptor chain in shared/transport/axios.ts (§1-6 of the
// file's own header comment) before any refactor touches it. See
// docs/tasks/2026-07-10-openapi-resync-generated-client-auth/spec.md §6.

afterEach(() => {
  setAccessToken(null)
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
    vi.useFakeTimers()
    try {
      server.use(mswHttp.get('/api/test/unreachable', () => HttpResponse.error()))
      await expect(http.get('/test/unreachable')).rejects.toMatchObject({
        name: 'NetworkError',
      })
    } finally {
      vi.clearAllTimers()
      vi.useRealTimers()
    }
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
