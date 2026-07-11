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

vi.mock('@shared/transport', () => {
  const channel = {
    subscribe: (name: string, handler: (ev: ChannelEvent) => void) => {
      ;(handlers[name] ??= []).push(handler)
      return () => {
        handlers[name] = (handlers[name] ?? []).filter((h) => h !== handler)
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

vi.mock('@slices/tenancy', () => ({
  tenancyKeys: { projectMembers: (p: string) => ['tenancy', 'members', p] },
  projectsApi: { listMembers: vi.fn(async () => []) },
}))

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

function makeObservation(id: string): Observation {
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
    created_at: '2024-01-01T00:00:00.000Z',
  } as Observation
}

function mountObs(opts?: {
  createdBy?: string | null
  boundAgents?: BoundAgentRef[]
}): {
  wrapper: VueWrapper
  api: ReturnType<typeof useObservations>
  store: ReturnType<typeof useConversationStore>
  qc: QueryClient
} {
  const pinia = createPinia()
  setActivePinia(pinia)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })

  const room = ref<Chatroom | undefined>({
    id: ROOM,
    created_by_user_id: opts?.createdBy === undefined ? CREATOR : opts.createdBy,
  } as Chatroom)
  const boundAgents = ref<BoundAgentRef[] | undefined>(
    opts?.boundAgents ?? [{ agent_id: OBS_AGENT, role: 'observer' }],
  )

  let api!: ReturnType<typeof useObservations>
  const Host = defineComponent({
    setup() {
      api = useObservations(ROOM, {
        room,
        projectId: ref('proj_1'),
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

  it('teardown unsubscribes its own handlers on unmount', async () => {
    const m = mountObs()
    wrapper = m.wrapper
    await flushPromises()
    expect((handlers['observation.started'] ?? []).length).toBe(1)

    m.wrapper.unmount()
    wrapper = null
    expect((handlers['observation.started'] ?? []).length).toBe(0)
  })
})
