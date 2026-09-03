// useObservations — creator resolution, live-event handling, pagination, and
// the W-1/W-3/W-4/W-5 regressions from the observer-fixes batch (§B.9).
//
// The transport is mocked with a per-event handler map (unlike the socket
// test's single-array capture) because this composable subscribes to four
// named observation.* events.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { computed, defineComponent, ref, type Ref } from 'vue'

import type { ChannelEvent } from '@shared/transport'
import type { BoundAgentRef } from '../api'
import type { Chatroom, Observation } from '../types'

const handlers: Record<string, Array<(ev: ChannelEvent) => void>> = {}
// F-8 ports the room path's reconnect reset, so the double has to carry the
// same status surface the real channel does. `onStatus` deliberately does NOT
// push the current value on subscribe (unlike `onDegraded`), so a fresh mount
// must not look like a reconnect.
const statusHandlers: Array<(connected: boolean) => void> = []

vi.mock('@shared/transport', () => {
  const channel = {
    subscribe: (name: string, handler: (ev: ChannelEvent) => void) => {
      ;(handlers[name] ??= []).push(handler)
      return () => {
        handlers[name] = (handlers[name] ?? []).filter((h) => h !== handler)
      }
    },
    onStatus: (handler: (connected: boolean) => void) => {
      statusHandlers.push(handler)
      return () => {
        const i = statusHandlers.indexOf(handler)
        if (i >= 0) statusHandlers.splice(i, 1)
      }
    },
    connect: () => {},
  }
  return { wsManager: { channel: () => channel } }
})

const sessionMe = vi.hoisted(() => ({ value: null as { id: string; is_admin: boolean } | null }))
vi.mock('@shared/stores/session', () => ({
  useSessionStore: () => ({
    get me() {
      return sessionMe.value
    },
  }),
}))

// F-3 removed `@slices/tenancy` from `useObservations.ts` entirely, so the mock
// that used to stand in for `projectsApi.listMembers` is gone with it. Asserting
// "the member list was never fetched" against a mock the module can no longer
// reach would pass whether or not the fix were present; that half of F-3 is now a
// structural guarantee (there is no import to call) and is checked by AC-6 and the
// boundaries lint rather than by a test that cannot fail.
const listObservationsMock = vi.hoisted(() => vi.fn())
const deleteObservationMock = vi.hoisted(() => vi.fn(async () => undefined))
const releaseObservationMock = vi.hoisted(() => vi.fn())
vi.mock('../api', () => ({
  listObservations: listObservationsMock,
  deleteObservation: deleteObservationMock,
  releaseObservation: releaseObservationMock,
}))

import { useObservations } from '../composables/useObservations'
import { useConversationStore } from '../stores/conversation'
import { convKeys } from '../queries'

const ROOM = 'cr_1'
const CREATOR = 'user_creator'
const OBS_AGENT = 'agent_obs'

function emit(name: string, ev: Record<string, unknown>): void {
  for (const h of [...(handlers[name] ?? [])]) h(ev as ChannelEvent)
}

function emitStatus(connected: boolean): void {
  for (const h of [...statusHandlers]) h(connected)
}

function makeObservation(id: string, createdAt = '2024-01-01T00:00:00.000Z'): Observation {
  return {
    id,
    chatroom_id: ROOM,
    agent_id: OBS_AGENT,
    content_md: `obs ${id}`,
    metadata: {},
    trigger: 'every_n_messages',
    trigger_message_id: null,
    released_at: null,
    release_target: null,
    released_by_user_id: null,
    created_at: createdAt,
  } as Observation
}

function mountObs(opts?: {
  createdBy?: string | null
  isModerator?: boolean
  boundAgents?: BoundAgentRef[]
  /** Rows already in the query cache at mount, i.e. returning to a room inside
   *  `gcTime`. The composable then starts with rows it has never watched. */
  cached?: Observation[]
}): {
  wrapper: VueWrapper
  api: ReturnType<typeof useObservations>
  store: ReturnType<typeof useConversationStore>
  qc: QueryClient
} {
  const pinia = createPinia()
  setActivePinia(pinia)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  if (opts?.cached) {
    qc.setQueryData(convKeys.observations(ROOM), { pages: [opts.cached], pageParams: [''] })
  }

  const room = ref<Chatroom | undefined>({
    id: ROOM,
    created_by_user_id: opts?.createdBy === undefined ? CREATOR : opts.createdBy,
    is_moderator: opts?.isModerator ?? false,
  } as Chatroom)
  const boundAgents = ref<BoundAgentRef[] | undefined>(
    opts?.boundAgents ?? [{ agent_id: OBS_AGENT, role: 'observer' }],
  )

  let api!: ReturnType<typeof useObservations>
  const Host = defineComponent({
    setup() {
      api = useObservations(ROOM, {
        room,
        boundAgents: boundAgents as Ref<BoundAgentRef[] | undefined>,
        agentNames: computed(() => ({ [OBS_AGENT]: 'Watcher' })),
      })
      return () => null
    },
  })
  const wrapper = mount(Host, {
    global: { plugins: [pinia, [VueQueryPlugin, { queryClient: qc }]] },
  })
  return { wrapper, api, store: useConversationStore(), qc }
}

describe('useObservations', () => {
  let wrapper: VueWrapper | null = null

  beforeEach(() => {
    for (const k of Object.keys(handlers)) delete handlers[k]
    statusHandlers.length = 0
    sessionMe.value = { id: CREATOR, is_admin: false }
    listObservationsMock.mockReset()
    listObservationsMock.mockResolvedValue([])
    deleteObservationMock.mockClear()
    releaseObservationMock.mockReset()
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
  })

  it('resolves isCreator by created_by match and admin', async () => {
    const m = mountObs()
    wrapper = m.wrapper
    expect(m.api.isCreator.value).toBe(true)

    m.wrapper.unmount()
    sessionMe.value = { id: 'someone_else', is_admin: false }
    const m2 = mountObs()
    wrapper = m2.wrapper
    expect(m2.api.isCreator.value).toBe(false)

    m2.wrapper.unmount()
    sessionMe.value = { id: 'admin', is_admin: true }
    const m3 = mountObs()
    wrapper = m3.wrapper
    expect(m3.api.isCreator.value).toBe(true)
  })

  // T-4 (F-3). For a NULL-creator room the server falls back to moderator
  // semantics, where an inherited ORG_OWNER role counts with no `project_members`
  // row at all (`access.py`). The client used to scan `projectsApi.listMembers`,
  // which serves `project_members` only — so it locked out exactly the owner the
  // server would have let in, and its unpaginated call dropped a genuine owner
  // past row 100 as well. `is_moderator` is on the DTO for this reason.
  it('T-4: a NULL-creator room trusts is_moderator, not the member list', async () => {
    sessionMe.value = { id: 'org_owner', is_admin: false }
    const m = mountObs({ createdBy: null, isModerator: true })
    wrapper = m.wrapper
    await flushPromises()

    expect(m.api.isCreator.value).toBe(true)
  })

  it('T-4: a NULL-creator room without moderator standing stays closed', async () => {
    sessionMe.value = { id: 'plain_member', is_admin: false }
    const m = mountObs({ createdBy: null, isModerator: false })
    wrapper = m.wrapper
    await flushPromises()

    expect(m.api.isCreator.value).toBe(false)
  })

  it('T-4: is_moderator does not open a room that has a real creator', async () => {
    // The moderator fallback is the NULL-creator path only. A non-creator
    // moderator of a room someone else created must not read its observations
    // (R28.02) — widening here would render a surface whose every request 403s.
    sessionMe.value = { id: 'someone_else', is_admin: false }
    const m = mountObs({ createdBy: CREATOR, isModerator: true })
    wrapper = m.wrapper
    await flushPromises()

    expect(m.api.isCreator.value).toBe(false)
  })

  it('W-1: a non-recipient admin polls, the real creator does not', async () => {
    vi.useFakeTimers()
    try {
      // Admin viewing another user's room → receives no WS events, so polls.
      sessionMe.value = { id: 'admin', is_admin: true }
      const admin = mountObs({ createdBy: CREATOR })
      wrapper = admin.wrapper
      await vi.advanceTimersByTimeAsync(0)
      const afterInitial = listObservationsMock.mock.calls.length
      await vi.advanceTimersByTimeAsync(31_000)
      expect(listObservationsMock.mock.calls.length).toBeGreaterThan(afterInitial)
      admin.wrapper.unmount()
      wrapper = null
      listObservationsMock.mockClear()

      // The real creator keeps the pure-WS path — no interval refetch.
      sessionMe.value = { id: CREATOR, is_admin: false }
      const creator = mountObs({ createdBy: CREATOR })
      wrapper = creator.wrapper
      await vi.advanceTimersByTimeAsync(0)
      const creatorInitial = listObservationsMock.mock.calls.length
      await vi.advanceTimersByTimeAsync(31_000)
      expect(listObservationsMock.mock.calls.length).toBe(creatorInitial)
    } finally {
      vi.useRealTimers()
    }
  })

  it('observation.started sets analyzing and clears prior error/skip', async () => {
    const m = mountObs()
    wrapper = m.wrapper
    await flushPromises()
    m.store.setObserverErrorKind(ROOM, OBS_AGENT, 'rate_limited')

    emit('observation.started', { chatroom_id: ROOM, agent_id: OBS_AGENT })

    expect(m.store.observerAnalyzing[ROOM]?.has(OBS_AGENT)).toBe(true)
    expect(m.store.observerErrors[ROOM]?.[OBS_AGENT]).toBeUndefined()
    expect(m.api.observerAgents.value[0]?.status).toBe('analyzing')
  })

  it('observation.skipped renders as skipped, not error', async () => {
    const m = mountObs()
    wrapper = m.wrapper
    await flushPromises()

    emit('observation.started', { chatroom_id: ROOM, agent_id: OBS_AGENT })
    emit('observation.skipped', { chatroom_id: ROOM, agent_id: OBS_AGENT, kind: 'no_input' })

    const entry = m.api.observerAgents.value[0]
    expect(entry?.status).toBe('skipped')
    expect(entry?.skipReason).toBe('no_input')
  })

  it('observation.failed records the kind as an error', async () => {
    const m = mountObs()
    wrapper = m.wrapper
    await flushPromises()

    emit('observation.failed', { chatroom_id: ROOM, agent_id: OBS_AGENT, kind: 'rate_limited' })

    const entry = m.api.observerAgents.value[0]
    expect(entry?.status).toBe('error')
    expect(entry?.errorReason).toBe('rate_limited')
  })

  it('ignores events for a different room', async () => {
    const m = mountObs()
    wrapper = m.wrapper
    await flushPromises()

    emit('observation.started', { chatroom_id: 'cr_other', agent_id: OBS_AGENT })

    expect(m.store.observerAnalyzing[ROOM]?.has(OBS_AGENT)).toBeFalsy()
  })

  it('unread counter increments only while the panel is closed', async () => {
    const m = mountObs()
    wrapper = m.wrapper
    await flushPromises()

    m.api.setPanelOpen(false)
    emit('observation.created', {
      chatroom_id: ROOM,
      agent_id: OBS_AGENT,
      observation_id: 'o1',
      created_at: '2024-01-01T00:00:00.000Z',
    })
    expect(m.api.unreadCount.value).toBe(1)

    m.api.setPanelOpen(true)
    expect(m.api.unreadCount.value).toBe(0)
    emit('observation.created', {
      chatroom_id: ROOM,
      agent_id: OBS_AGENT,
      observation_id: 'o2',
      created_at: '2024-01-01T00:00:00.000Z',
    })
    expect(m.api.unreadCount.value).toBe(0)
  })

  it('released patch is immutable (replaces objects, no in-place mutation)', async () => {
    const obs = makeObservation('o1')
    listObservationsMock.mockResolvedValue([obs])
    releaseObservationMock.mockResolvedValue({
      ...obs,
      released_at: '2024-02-02T00:00:00.000Z',
      release_target: { kind: 'room', message_id: 'm1' },
    })
    const m = mountObs()
    wrapper = m.wrapper
    await flushPromises()

    const before = m.api.observations.value[0]
    expect(before?.released_at).toBeNull()
    await m.api.release('o1', { target: 'room' })
    const after = m.api.observations.value[0]

    // A new object (not an in-place mutation) with the release applied.
    expect(after).not.toBe(before)
    expect(after?.released_at).not.toBeNull()
    expect(after?.release_target).toEqual({ kind: 'room', message_id: 'm1' })
  })

  it('W-5: delete invalidates the query so hasMore stays authoritative', async () => {
    const obs = makeObservation('o1')
    listObservationsMock.mockResolvedValue([obs])
    const m = mountObs()
    wrapper = m.wrapper
    await flushPromises()
    const spy = vi.spyOn(m.qc, 'invalidateQueries')

    await m.api.remove('o1')

    expect(deleteObservationMock).toHaveBeenCalledWith(ROOM, 'o1')
    expect(spy).toHaveBeenCalledWith({ queryKey: convKeys.observations(ROOM) })
  })

  // FU-8's client half. The room channel no longer carries `chatroom.updated`
  // for a write non-creators cannot see, so the creator's own other tabs are
  // refreshed over their private user channel instead.
  it('chatroom.updated on the user channel refreshes the room and roster', async () => {
    const m = mountObs()
    wrapper = m.wrapper
    await flushPromises()
    const spy = vi.spyOn(m.qc, 'invalidateQueries')

    emit('chatroom.updated', { chatroom_id: ROOM })

    expect(spy).toHaveBeenCalledWith({ queryKey: convKeys.chatroom(ROOM) })
    expect(spy).toHaveBeenCalledWith({ queryKey: convKeys.chatroomAgents(ROOM) })
  })

  it('ignores a chatroom.updated naming another room', async () => {
    const m = mountObs()
    wrapper = m.wrapper
    await flushPromises()
    const spy = vi.spyOn(m.qc, 'invalidateQueries')

    emit('chatroom.updated', { chatroom_id: 'cr_other' })

    expect(spy).not.toHaveBeenCalledWith({ queryKey: convKeys.chatroom(ROOM) })
  })

  it('teardown unsubscribes its own handlers on unmount', async () => {
    const m = mountObs()
    wrapper = m.wrapper
    await flushPromises()
    expect((handlers['observation.started'] ?? []).length).toBe(1)

    m.wrapper.unmount()
    wrapper = null
    expect((handlers['observation.started'] ?? []).length).toBe(0)
  })

  it('exposes the observer surface when observations exist but no observer is bound', async () => {
    listObservationsMock.mockResolvedValue([makeObservation('o1')])
    const m = mountObs({ boundAgents: [{ agent_id: 'agent_normal', role: 'normal' }] })
    wrapper = m.wrapper
    await flushPromises()

    expect(m.api.observerAgents.value.length).toBe(0)
    expect(m.api.hasObserverSurface.value).toBe(true)
  })

  // ---- phase 2: observer status truthfulness -------------------------------

  // T-7 (F-6). `observation.*` reaches only the literal creator's user channel
  // (`observation_service.recipient_user_id`), so every other viewer that gets
  // past the REST gate — an admin, a NULL-creator room's moderator — has no
  // status feed at all. Falling through to 'idle' turned that silence into an
  // affirmative claim about a worker they cannot hear.
  it('T-7: a viewer with no event feed sees unknown, the creator sees idle', async () => {
    sessionMe.value = { id: 'admin', is_admin: true }
    const admin = mountObs({ createdBy: CREATOR })
    wrapper = admin.wrapper
    await flushPromises()
    expect(admin.api.observerAgents.value[0]?.status).toBe('unknown')

    admin.wrapper.unmount()
    sessionMe.value = { id: CREATOR, is_admin: false }
    const creator = mountObs({ createdBy: CREATOR })
    wrapper = creator.wrapper
    await flushPromises()
    expect(creator.api.observerAgents.value[0]?.status).toBe('idle')
  })

  it('T-7: a NULL-creator room has no recipient, so even its moderator sees unknown', async () => {
    // `_emit_observation_event` publishes nothing when `created_by_user_id` is
    // NULL — there is no user channel to address — so the moderator-fallback
    // viewer is in exactly the same position as the admin.
    sessionMe.value = { id: 'org_owner', is_admin: false }
    const m = mountObs({ createdBy: null, isModerator: true })
    wrapper = m.wrapper
    await flushPromises()

    expect(m.api.isCreator.value).toBe(true)
    expect(m.api.observerAgents.value[0]?.status).toBe('unknown')
  })

  // T-8 (F-8), first guard. Redis pub/sub does not replay, so a terminal frame
  // published while the socket was down is simply gone; the room path solved
  // this with a reconnect reset (`useChatroomSocket.ts` onStatus) and the
  // observer path never got one.
  it('T-8: a reconnect clears a stranded analyzing flag and refetches', async () => {
    const m = mountObs()
    wrapper = m.wrapper
    await flushPromises()

    emit('observation.started', { chatroom_id: ROOM, agent_id: OBS_AGENT })
    expect(m.store.observerAnalyzing[ROOM]?.has(OBS_AGENT)).toBe(true)
    const before = listObservationsMock.mock.calls.length

    emitStatus(true)
    await flushPromises()

    expect(m.store.observerAnalyzing[ROOM]?.has(OBS_AGENT)).toBeFalsy()
    expect(m.api.observerAgents.value[0]?.status).toBe('idle')
    expect(listObservationsMock.mock.calls.length).toBeGreaterThan(before)
  })

  it('T-8: a reconnect for a non-creator issues no request', async () => {
    // The user channel is subscribed for every viewer, but the observations
    // endpoint 403s for anyone but the creator (R28.03) — the query's `enabled`
    // gate is what keeps the client from asking. A reconcile that bypassed it
    // would turn every reconnect into a speculative 403.
    sessionMe.value = { id: 'plain_member', is_admin: false }
    const m = mountObs({ createdBy: CREATOR })
    wrapper = m.wrapper
    await flushPromises()
    expect(m.api.isCreator.value).toBe(false)
    listObservationsMock.mockClear()

    emitStatus(true)
    await flushPromises()

    expect(listObservationsMock).not.toHaveBeenCalled()
  })

  it('T-8: a disconnect on its own neither clears nor refetches', async () => {
    const m = mountObs()
    wrapper = m.wrapper
    await flushPromises()

    emit('observation.started', { chatroom_id: ROOM, agent_id: OBS_AGENT })
    const before = listObservationsMock.mock.calls.length

    emitStatus(false)
    await flushPromises()

    expect(m.store.observerAnalyzing[ROOM]?.has(OBS_AGENT)).toBe(true)
    expect(listObservationsMock.mock.calls.length).toBe(before)
  })

  // T-9 (F-8), second guard. A hard worker kill between the started emit and
  // the terminal emit leaves no frame to lose and no reconnect to recover it,
  // so only a client-side deadline resolves it — the same trade the room path
  // already makes at 120s.
  it('T-9: the watchdog clears analyzing and reports timeout', async () => {
    vi.useFakeTimers()
    try {
      const m = mountObs()
      wrapper = m.wrapper
      await vi.advanceTimersByTimeAsync(0)

      emit('observation.started', { chatroom_id: ROOM, agent_id: OBS_AGENT })
      await vi.advanceTimersByTimeAsync(119_000)
      expect(m.api.observerAgents.value[0]?.status).toBe('analyzing')

      await vi.advanceTimersByTimeAsync(2_000)

      const entry = m.api.observerAgents.value[0]
      expect(entry?.status).toBe('error')
      expect(entry?.errorReason).toBe('timeout')
    } finally {
      vi.useRealTimers()
    }
  })

  it('T-9: a terminal event disarms the watchdog', async () => {
    vi.useFakeTimers()
    try {
      const m = mountObs()
      wrapper = m.wrapper
      await vi.advanceTimersByTimeAsync(0)

      emit('observation.started', { chatroom_id: ROOM, agent_id: OBS_AGENT })
      emit('observation.created', {
        chatroom_id: ROOM,
        agent_id: OBS_AGENT,
        observation_id: 'o1',
        created_at: '2024-01-01T00:00:00.000Z',
      })
      await vi.advanceTimersByTimeAsync(200_000)

      // Not 'error': the turn finished. A watchdog that fired anyway would
      // report a healthy observer as timed out for the rest of the page's life.
      expect(m.api.observerAgents.value[0]?.status).toBe('idle')
      expect(m.store.observerErrors[ROOM]?.[OBS_AGENT]).toBeUndefined()
    } finally {
      vi.useRealTimers()
    }
  })

  // Code review, finding 1. §9 accepts that the watchdog can fire on a slow but
  // healthy observer specifically because "a later observation.created would
  // correct it" — and it did not. `observation.started` clears the error kind;
  // no terminal handler did, so a spurious `timeout` outlived the very output
  // that disproved it and sat in the roster beside the row in the list below.
  it('a created observation clears a watchdog timeout it disproves', async () => {
    vi.useFakeTimers()
    try {
      const m = mountObs()
      wrapper = m.wrapper
      await vi.advanceTimersByTimeAsync(0)

      emit('observation.started', { chatroom_id: ROOM, agent_id: OBS_AGENT })
      await vi.advanceTimersByTimeAsync(121_000)
      expect(m.api.observerAgents.value[0]?.status).toBe('error')

      // The analysis was merely slow; the observation lands after the deadline.
      emit('observation.created', {
        chatroom_id: ROOM,
        agent_id: OBS_AGENT,
        observation_id: 'o1',
        created_at: '2024-01-03T00:00:00.000Z',
      })

      expect(m.api.observerAgents.value[0]?.status).toBe('idle')
      expect(m.store.observerErrors[ROOM]?.[OBS_AGENT]).toBeUndefined()
    } finally {
      vi.useRealTimers()
    }
  })

  it('a created observation clears a stale skip on the same observer', async () => {
    const m = mountObs()
    wrapper = m.wrapper
    await flushPromises()

    emit('observation.skipped', { chatroom_id: ROOM, agent_id: OBS_AGENT, kind: 'no_input' })
    expect(m.api.observerAgents.value[0]?.status).toBe('skipped')

    emit('observation.created', {
      chatroom_id: ROOM,
      agent_id: OBS_AGENT,
      observation_id: 'o1',
      created_at: '2024-01-03T00:00:00.000Z',
    })

    expect(m.api.observerAgents.value[0]?.status).toBe('idle')
  })

  // Code review, finding 2. The WS handler raises the badge synchronously but
  // updates the cache through an async invalidate, and the high-water mark only
  // advances when that write lands. A reconnect in the gap — the flaky
  // connection this reconcile exists for — re-counted the same row.
  it('a reconnect before the created refetch lands does not double-count', async () => {
    listObservationsMock.mockResolvedValue([makeObservation('o1', '2024-01-01T00:00:00.000Z')])
    const m = mountObs()
    wrapper = m.wrapper
    await flushPromises()
    m.api.setPanelOpen(false)

    // The frame arrives; the badge rises. Its refetch has not resolved yet.
    listObservationsMock.mockResolvedValue([
      makeObservation('o2', '2024-01-02T00:00:00.000Z'),
      makeObservation('o1', '2024-01-01T00:00:00.000Z'),
    ])
    emit('observation.created', {
      chatroom_id: ROOM,
      agent_id: OBS_AGENT,
      observation_id: 'o2',
      created_at: '2024-01-02T00:00:00.000Z',
    })
    expect(m.api.unreadCount.value).toBe(1)

    emitStatus(true)
    await flushPromises()

    // One observation, one badge — not two.
    expect(m.api.unreadCount.value).toBe(1)
  })

  // T-10 (F-11). The list endpoint does not filter released rows, so a poll
  // response issued before the release carries `released_at: null` and lands
  // after the optimistic patch — reviving the Release control on a row that is
  // already out. `remove()` has invalidated since W-5; `release()` did not.
  it('T-10: release invalidates so the server is the last writer', async () => {
    const obs = makeObservation('o1')
    listObservationsMock.mockResolvedValue([obs])
    releaseObservationMock.mockResolvedValue({
      ...obs,
      released_at: '2024-02-02T00:00:00.000Z',
      release_target: { kind: 'room', message_id: 'm1' },
    })
    const m = mountObs()
    wrapper = m.wrapper
    await flushPromises()
    const spy = vi.spyOn(m.qc, 'invalidateQueries')

    await m.api.release('o1', { target: 'room' })

    expect(spy).toHaveBeenCalledWith({ queryKey: convKeys.observations(ROOM) })
  })

  // T-12 (F-13). The badge was incremented only by the WS handler, so an
  // `observation.created` lost to a socket gap raised nothing even once the
  // reconnect refetch recovered the row itself.
  it('T-12: a reconnect refetch that returns a newer row raises the badge', async () => {
    listObservationsMock.mockResolvedValue([makeObservation('o1', '2024-01-01T00:00:00.000Z')])
    const m = mountObs()
    wrapper = m.wrapper
    await flushPromises()
    m.api.setPanelOpen(false)
    expect(m.api.unreadCount.value).toBe(0)

    listObservationsMock.mockResolvedValue([
      makeObservation('o2', '2024-01-02T00:00:00.000Z'),
      makeObservation('o1', '2024-01-01T00:00:00.000Z'),
    ])
    emitStatus(true)
    await flushPromises()

    expect(m.api.unreadCount.value).toBe(1)
  })

  it('T-12: a reconnect refetch returning only known rows leaves the badge alone', async () => {
    // The comparison is against what has already been rendered, not against the
    // previous page contents — otherwise a refetch that merely re-serves known
    // rows would inflate the badge on every reconnect.
    listObservationsMock.mockResolvedValue([makeObservation('o1', '2024-01-01T00:00:00.000Z')])
    const m = mountObs()
    wrapper = m.wrapper
    await flushPromises()
    m.api.setPanelOpen(false)

    emitStatus(true)
    await flushPromises()

    expect(m.api.unreadCount.value).toBe(0)
  })

  it('T-12: a reconnect before the mount refetch settles badges against the cached rows', async () => {
    // Returning to a room inside `gcTime` serves the query from cache, so the
    // panel renders rows the composable has never watched. The high-water mark is
    // seeded by a watcher, and a lazy one does not run until the *next* cache
    // write — so a socket drop in that window left the mark null, `baseline ===
    // null` returned early, and every observation written during the outage went
    // unbadged. Exactly the gap F-13 exists to close, in the one case where the
    // panel already had rows to compare against.
    const seen = makeObservation('o1', '2024-01-01T00:00:00.000Z')
    listObservationsMock.mockResolvedValue([
      makeObservation('o2', '2024-01-02T00:00:00.000Z'),
      seen,
    ])
    const m = mountObs({ cached: [seen] })
    wrapper = m.wrapper
    m.api.setPanelOpen(false)

    // No `flushPromises` first: the reconnect lands while the mount refetch is
    // still in flight, which is the whole point.
    emitStatus(true)
    await flushPromises()

    expect(m.api.unreadCount.value).toBe(1)
  })

  it('T-12: an open panel is not badged by a reconnect refetch', async () => {
    listObservationsMock.mockResolvedValue([makeObservation('o1', '2024-01-01T00:00:00.000Z')])
    const m = mountObs()
    wrapper = m.wrapper
    await flushPromises()
    m.api.setPanelOpen(true)

    listObservationsMock.mockResolvedValue([
      makeObservation('o2', '2024-01-02T00:00:00.000Z'),
      makeObservation('o1', '2024-01-01T00:00:00.000Z'),
    ])
    emitStatus(true)
    await flushPromises()

    expect(m.api.unreadCount.value).toBe(0)
  })

  // F-14's client half: the row a second session deleted has to leave this
  // session's list without a reload.
  it('observation.deleted drops the row from the cache', async () => {
    listObservationsMock.mockResolvedValue([makeObservation('o1'), makeObservation('o2')])
    const m = mountObs()
    wrapper = m.wrapper
    await flushPromises()
    expect(m.api.observations.value.map((o) => o.id)).toEqual(['o1', 'o2'])

    listObservationsMock.mockResolvedValue([makeObservation('o2')])
    emit('observation.deleted', { chatroom_id: ROOM, observation_id: 'o1' })
    await flushPromises()

    expect(m.api.observations.value.map((o) => o.id)).toEqual(['o2'])
  })

  it('observation.deleted for another room is ignored', async () => {
    listObservationsMock.mockResolvedValue([makeObservation('o1')])
    const m = mountObs()
    wrapper = m.wrapper
    await flushPromises()

    emit('observation.deleted', { chatroom_id: 'cr_other', observation_id: 'o1' })
    await flushPromises()

    expect(m.api.observations.value.map((o) => o.id)).toEqual(['o1'])
  })

  it('keeps the observer surface hidden for a creator with neither bindings nor observations', async () => {
    listObservationsMock.mockResolvedValue([])
    const m = mountObs({ boundAgents: [] })
    wrapper = m.wrapper
    await flushPromises()

    expect(m.api.hasObserverSurface.value).toBe(false)
  })
})
