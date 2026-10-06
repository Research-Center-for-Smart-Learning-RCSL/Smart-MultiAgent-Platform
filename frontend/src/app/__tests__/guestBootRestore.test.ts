// A reload or a new tab recovers a live guest session (F-7, Q-1): the account
// refresh is tried first, then the room's guest refresh when this browser holds
// the room's hint. Whatever the outcome, a guest whose room is in the URL lands
// on the room, never on /login (AC-1, AC-9).
// docs/tasks/2026-10-05-guest-frontend-session-lifecycle

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { http, HttpResponse } from 'msw'
import { server } from '../../../tests/mocks/server'
import {
  clearGuestContext,
  fetchWsTicket,
  getAccessToken,
  getGuestChatroomId,
  guestSessionEnd,
  setAccessToken,
} from '@shared/transport'
import { useSessionStore } from '@slices/identity'
import { preferAccountOverPendingGuest, restoreSessionAtBoot } from '../boot'
import { guardRoute, router } from '../router'

const ROOM = '0f8e2b1c-aaaa-4bbb-8ccc-0123456789ab'
const HINT = `smap:guest:${ROOM}`
const NO_ACCOUNT = http.post('/api/auth/refresh', () =>
  HttpResponse.json({ type: 'https://smap.local/problems/auth/required', title: 'x', status: 401 }, { status: 401 }),
)

let guestRefreshes = 0
function guestRefreshAnswers(answer: () => Response): void {
  guestRefreshes = 0
  server.use(
    http.post(`/api/guest/${ROOM}/refresh`, () => {
      guestRefreshes += 1
      return answer()
    }),
  )
}

function holdHint(): void {
  localStorage.setItem(HINT, JSON.stringify({ browser_id: 'b', guest_session_id: 'g', display_name: 'Alice' }))
}

function roomDecision(): unknown {
  return guardRoute(router.resolve(`/chatrooms/${ROOM}`))
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
})

afterEach(() => {
  setAccessToken(null)
  clearGuestContext()
  localStorage.clear()
})

describe('restoreSessionAtBoot', () => {
  it.each([`/chatrooms/${ROOM}`, `/c/${ROOM}`, `/c/${ROOM.toUpperCase()}`])(
    'resumes the guest session for %s when the browser holds the hint',
    async (path) => {
      server.use(NO_ACCOUNT)
      guestRefreshAnswers(() => HttpResponse.json({ access_token: 'guest-jwt' }))
      holdHint()

      await restoreSessionAtBoot(path)

      expect(guestRefreshes).toBe(1)
      expect(getAccessToken()).toBe('guest-jwt')
      expect(getGuestChatroomId()).toBe(ROOM)
      expect(roomDecision()).toBe(true)
    },
  )

  it('does nothing without the hint', async () => {
    server.use(NO_ACCOUNT)
    guestRefreshAnswers(() => HttpResponse.json({ access_token: 'guest-jwt' }))

    await restoreSessionAtBoot(`/chatrooms/${ROOM}`)

    expect(guestRefreshes).toBe(0)
    expect(getGuestChatroomId()).toBeNull()
    expect(roomDecision()).toMatchObject({ name: 'identity.login' })
  })

  it('does nothing outside a room URL', async () => {
    server.use(NO_ACCOUNT)
    guestRefreshAnswers(() => HttpResponse.json({ access_token: 'guest-jwt' }))
    holdHint()

    await restoreSessionAtBoot(`/chatrooms/${ROOM}/settings`)

    expect(guestRefreshes).toBe(0)
  })

  it('lets the account win when its refresh succeeds', async () => {
    guestRefreshAnswers(() => HttpResponse.json({ access_token: 'guest-jwt' }))
    holdHint()

    await restoreSessionAtBoot(`/chatrooms/${ROOM}`)

    expect(guestRefreshes).toBe(0)
    expect(useSessionStore().isAuthenticated).toBe(true)
    expect(getGuestChatroomId()).toBeNull()
  })

  it('lands an expired session on the room, not /login, and drops the hint', async () => {
    server.use(NO_ACCOUNT)
    guestRefreshAnswers(() =>
      HttpResponse.json(
        { type: 'https://smap.local/problems/conversation/guest-token-invalid', title: 'x', status: 404 },
        { status: 404 },
      ),
    )
    holdHint()

    await restoreSessionAtBoot(`/chatrooms/${ROOM}`)

    expect(guestSessionEnd.value).toBe('expired')
    expect(localStorage.getItem(HINT)).toBeNull()
    expect(roomDecision()).toBe(true)
  })

  it('lands a disabled session on the room and keeps the hint', async () => {
    server.use(NO_ACCOUNT)
    guestRefreshAnswers(() =>
      HttpResponse.json(
        { type: 'https://smap.local/problems/conversation/guest-access-disabled', title: 'x', status: 403 },
        { status: 403 },
      ),
    )
    holdHint()

    await restoreSessionAtBoot(`/chatrooms/${ROOM}`)

    expect(guestSessionEnd.value).toBe('disabled')
    expect(localStorage.getItem(HINT)).not.toBeNull()
    expect(roomDecision()).toBe(true)
  })

  it('lands an offline guest on the room and resumes once the network returns', async () => {
    server.use(NO_ACCOUNT)
    guestRefreshAnswers(() => HttpResponse.error())
    holdHint()

    await restoreSessionAtBoot(`/chatrooms/${ROOM}`)

    expect(getAccessToken()).toBeNull()
    expect(guestSessionEnd.value).toBeNull()
    expect(roomDecision()).toBe(true)

    // The socket's next ticket request is the room's retry.
    guestRefreshAnswers(() => HttpResponse.json({ access_token: 'guest-jwt' }))
    server.use(http.post('/api/guest/ws-ticket', () => HttpResponse.json({ ticket: 't', expires_in: 30 })))
    expect(await fetchWsTicket()).toBe('t')
    expect(getAccessToken()).toBe('guest-jwt')
  })

  it('hands an offline-restored room back to the account once the network returns', async () => {
    let accountUp = false
    server.use(
      http.post('/api/auth/refresh', () =>
        accountUp
          ? HttpResponse.json({ access_token: 'user', refresh_token: 'r', expires_in: 60 })
          : HttpResponse.error(),
      ),
    )
    guestRefreshAnswers(() => HttpResponse.error())
    holdHint()
    await restoreSessionAtBoot(`/chatrooms/${ROOM}`)
    expect(getGuestChatroomId()).toBe(ROOM)

    accountUp = true
    guestRefreshAnswers(() => HttpResponse.json({ access_token: 'guest-jwt' }))
    const reload = vi.fn()
    expect(await preferAccountOverPendingGuest(reload)).toBe(true)

    expect(reload).toHaveBeenCalledTimes(1)
    expect(guestRefreshes).toBe(0)
    expect(useSessionStore().isAuthenticated).toBe(true)
    expect(getGuestChatroomId()).toBeNull()
  })

  it('leaves an offline-restored room to the guest when there is no account', async () => {
    server.use(NO_ACCOUNT)
    guestRefreshAnswers(() => HttpResponse.error())
    holdHint()
    await restoreSessionAtBoot(`/chatrooms/${ROOM}`)

    const reload = vi.fn()
    expect(await preferAccountOverPendingGuest(reload)).toBe(false)
    expect(reload).not.toHaveBeenCalled()
    expect(getGuestChatroomId()).toBe(ROOM)
  })

  it('wires the account-first check into the socket ticket path at boot', async () => {
    server.use(NO_ACCOUNT)
    guestRefreshAnswers(() => HttpResponse.error())
    holdHint()
    await restoreSessionAtBoot(`/chatrooms/${ROOM}`)

    let accountRefreshes = 0
    server.use(
      http.post('/api/auth/refresh', () => {
        accountRefreshes += 1
        return HttpResponse.json({ type: 'https://smap.local/problems/auth/required', title: 'x', status: 401 }, { status: 401 })
      }),
    )
    guestRefreshAnswers(() => HttpResponse.json({ access_token: 'guest-jwt' }))
    server.use(http.post('/api/guest/ws-ticket', () => HttpResponse.json({ ticket: 't', expires_in: 30 })))

    expect(await fetchWsTicket()).toBe('t')
    expect(accountRefreshes).toBe(1)
  })

  it('does not let a guest context for one room open another', async () => {
    server.use(NO_ACCOUNT)
    guestRefreshAnswers(() => HttpResponse.error())
    holdHint()
    await restoreSessionAtBoot(`/chatrooms/${ROOM}`)

    const other = guardRoute(router.resolve('/chatrooms/11111111-2222-4333-8444-555555555555'))
    expect(other).toMatchObject({ name: 'identity.login' })
  })
})
