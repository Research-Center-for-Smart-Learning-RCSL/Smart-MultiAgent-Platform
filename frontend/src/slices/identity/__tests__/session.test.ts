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

  it('does not hydrate while a guest context is held, even with no guest token', async () => {
    let refreshes = 0
    server.use(
      http.post('/api/auth/refresh', () => {
        refreshes += 1
        return HttpResponse.json({ access_token: 'user', refresh_token: 'r', expires_in: 60 })
      }),
    )
    setGuestContext(ROOM)
    const session = useSessionStore()

    await session.hydrate()

    expect(refreshes).toBe(0)
    expect(session.isAuthenticated).toBe(false)
    expect(getGuestChatroomId()).toBe(ROOM)
  })
})
