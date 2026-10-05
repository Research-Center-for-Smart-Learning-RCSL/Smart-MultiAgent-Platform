// The guest context belongs to the guest token: a user token replacing it, or the
// tab's user state being cleared, takes the context with it (F-9, Q-7). And a tab
// holding a guest context, live or ended, is never re-hydrated into an account on
// focus (F-8, Q-3).
// docs/tasks/2026-10-05-guest-frontend-session-lifecycle

import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import {
  clearGuestContext,
  getAccessToken,
  getGuestChatroomId,
  markGuestSessionEnded,
  setAccessToken,
  setGuestContext,
} from '@shared/transport'
import { useSessionStore } from '../stores/session'

const ROOM = '0f8e2b1c-aaaa-4bbb-8ccc-0123456789ab'

beforeEach(() => {
  setActivePinia(createPinia())
})

afterEach(() => {
  setAccessToken(null)
  clearGuestContext()
})

describe('session store and the guest context', () => {
  it('applying user tokens clears the guest context', () => {
    setGuestContext(ROOM)
    useSessionStore().applyTokens({ access_token: 'user', token_type: 'bearer', expires_in: 60 })
    expect(getGuestChatroomId()).toBeNull()
    expect(getAccessToken()).toBe('user')
  })

  it('logging in clears the guest context', async () => {
    setGuestContext(ROOM)
    await useSessionStore().login('a@b.c', 'pw')
    expect(getGuestChatroomId()).toBeNull()
  })

  it('clearing the tab clears the guest context', () => {
    setGuestContext(ROOM)
    useSessionStore().clear()
    expect(getGuestChatroomId()).toBeNull()
  })

  it('does not hydrate while the room shows how its guest session ended', async () => {
    let refreshes = 0
    server.use(
      http.post('/api/auth/refresh', () => {
        refreshes += 1
        return HttpResponse.json({ access_token: 'user', refresh_token: 'r', expires_in: 60 })
      }),
    )
    setGuestContext(ROOM)
    markGuestSessionEnded('expired')
    const session = useSessionStore()

    await session.hydrate()

    expect(refreshes).toBe(0)
    expect(session.isAuthenticated).toBe(false)
    expect(getGuestChatroomId()).toBe(ROOM)
  })

  it('lets the account take over a guest restore still pending for want of a network', async () => {
    setGuestContext(ROOM)
    const session = useSessionStore()

    await session.hydrate()

    expect(session.isAuthenticated).toBe(true)
    expect(getGuestChatroomId()).toBeNull()
  })

  it('keeps a pending guest restore when there is no account', async () => {
    server.use(
      http.post('/api/auth/refresh', () =>
        HttpResponse.json({ type: 'https://smap.local/problems/auth/required', title: 'x', status: 401 }, { status: 401 }),
      ),
    )
    setGuestContext(ROOM)
    const session = useSessionStore()

    await session.hydrate()

    expect(session.isAuthenticated).toBe(false)
    expect(getGuestChatroomId()).toBe(ROOM)
  })

  it.each([
    ['fails', 401],
    ['succeeds', 200],
  ] as const)(
    'leaves a guest session entered while a focus hydrate was in flight alone when the refresh %s',
    async (_, status) => {
      let answer!: () => void
      const gate = new Promise<void>((r) => {
        answer = r
      })
      let arrived!: () => void
      const sent = new Promise<void>((r) => {
        arrived = r
      })
      server.use(
        http.post('/api/auth/refresh', async () => {
          arrived()
          await gate
          return status === 200
            ? HttpResponse.json({ access_token: 'user', refresh_token: 'r', expires_in: 60 })
            : HttpResponse.json({ type: 'https://smap.local/problems/auth/required', title: 'x', status }, { status })
        }),
      )
      const session = useSessionStore()

      const hydrating = session.hydrate()
      await sent
      setGuestContext(ROOM)
      setAccessToken('guest-token')
      answer()
      await hydrating

      expect(getAccessToken()).toBe('guest-token')
      expect(getGuestChatroomId()).toBe(ROOM)
      expect(session.isAuthenticated).toBe(false)
    },
  )
})
