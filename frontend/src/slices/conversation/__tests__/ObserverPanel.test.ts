import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import ObserverPanel from '../components/ObserverPanel.vue'
import type { ObserverEntry } from '../composables/useObservations'
import type { Observation } from '../types'

function baseProps(
  overrides: Partial<{
    observerAgents: ObserverEntry[]
    observations: Observation[]
    isError: boolean
    rosterKnown: boolean
  }> = {},
) {
  return {
    observerAgents: [] as ObserverEntry[],
    observations: [] as Observation[],
    loading: false,
    hasMore: false,
    loadingMore: false,
    agentNames: { a1: 'Watcher' },
    isError: false,
    // The default is "the roster has settled", so every pre-existing case keeps
    // asserting what it asserted before F-10 added the gate.
    rosterKnown: true,
    ...overrides,
  }
}

function makeObservation(id: string): Observation {
  return {
    id,
    chatroom_id: 'cr_1',
    agent_id: 'a1',
    content_md: `obs ${id}`,
    metadata: {},
    trigger: 'every_n_messages',
    trigger_message_id: null,
    released_at: null,
    release_target: null,
    released_by_user_id: null,
    created_at: '2026-01-01T00:00:00.000Z',
  }
}

describe('ObserverPanel roster status (W-4)', () => {
  it('renders a mapped kind label for a failed observer', async () => {
    const wrapper = await renderView(ObserverPanel, {
      props: baseProps({
        observerAgents: [{ id: 'a1', name: 'Watcher', status: 'error', errorReason: 'rate_limited' }],
      }),
    })
    const status = wrapper.find('.obs-panel__roster-status')
    // Reuses the shared agent-error map → conversation.chatroom.agentRateLimited,
    // never the generic "error" literal.
    expect(status.text()).not.toBe('error')
    expect(status.classes()).toContain('obs-panel__roster-status--error')
  })

  // T-7's rendered half (F-6). 'unknown' names an absent feed, not a worker
  // state, so it must neither borrow the error styling nor go unexplained: an
  // admin reading a bare word has no way to tell it from a third failure kind.
  it('renders an unknown status with its own styling and an explanation', async () => {
    const wrapper = await renderView(ObserverPanel, {
      props: baseProps({
        observerAgents: [{ id: 'a1', name: 'Watcher', status: 'unknown' }],
      }),
    })
    const status = wrapper.find('.obs-panel__roster-status')

    expect(status.text()).toBe('conversation.observers.status.unknown')
    expect(status.classes()).toContain('obs-panel__roster-status--unknown')
    expect(status.classes()).not.toContain('obs-panel__roster-status--error')
    expect(wrapper.find('.obs-panel__roster-item').attributes('title')).toBe(
      'conversation.observers.unknownStatusHint',
    )
  })

  it('renders a benign skip as skipped, not error', async () => {
    const wrapper = await renderView(ObserverPanel, {
      props: baseProps({
        observerAgents: [{ id: 'a1', name: 'Watcher', status: 'skipped', skipReason: 'no_input' }],
      }),
    })
    const status = wrapper.find('.obs-panel__roster-status')
    expect(status.classes()).toContain('obs-panel__roster-status--skipped')
    expect(status.classes()).not.toContain('obs-panel__roster-status--error')
  })
})

describe('ObserverPanel roster-empty note (observation-binding-cleanup T-4)', () => {
  it('explains an empty roster when past observations remain', async () => {
    const wrapper = await renderView(ObserverPanel, {
      props: baseProps({ observerAgents: [], observations: [makeObservation('o1')] }),
    })

    expect(wrapper.text()).toContain('obs o1')
    const alert = wrapper.find('.s-alert')
    expect(alert.exists()).toBe(true)
  })
})

// T-5 (F-7). The observations query sets `retry: false` and collapses undefined
// data to `[]`, and no global QueryCache.onError compensates — so a failed fetch
// painted the empty state, whose copy ("Observers write here after they analyze
// the conversation") asserts a fact the client never established. The panel also
// stays mounted while the list is dead, because the surface gate is satisfied by
// the surviving bound-agents query.
describe('ObserverPanel query failure (F-7)', () => {
  it('renders the error state with a retry, not the empty state', async () => {
    const wrapper = await renderView(ObserverPanel, {
      props: baseProps({ isError: true }),
    })

    expect(wrapper.text()).toContain('conversation.observers.loadError')
    expect(wrapper.text()).not.toContain('conversation.observers.emptyTitle')
    expect(wrapper.find('.s-alert').exists()).toBe(true)
  })

  it('emits retry when the retry control is pressed', async () => {
    const wrapper = await renderView(ObserverPanel, {
      props: baseProps({ isError: true }),
    })

    await wrapper.find('.s-alert button').trigger('click')

    expect(wrapper.emitted('retry')).toBeTruthy()
  })

  it('keeps cached rows visible beside the banner', async () => {
    // A failed background refetch still holds the rows it fetched last time.
    // The banner reports the transport problem; blanking the list would throw
    // away readable observations to do it.
    const wrapper = await renderView(ObserverPanel, {
      props: baseProps({ isError: true, observations: [makeObservation('o1')] }),
    })

    expect(wrapper.text()).toContain('conversation.observers.loadError')
    expect(wrapper.text()).toContain('obs o1')
  })
})

// T-6 (F-10). The alert sat above the loading gate and keyed off roster length
// alone, so it also fired when the bound-agents query had simply not answered
// yet, when that query had failed outright, and when an observer bound in
// another session was missing from a cache nothing invalidates. It claims an
// unbinding happened; it must only do so when the roster is actually known.
describe('ObserverPanel no-observer-bound alert (F-10)', () => {
  it('stays silent while the roster is unknown', async () => {
    const wrapper = await renderView(ObserverPanel, {
      props: baseProps({
        rosterKnown: false,
        observerAgents: [],
        observations: [makeObservation('o1')],
      }),
    })

    expect(wrapper.text()).not.toContain('conversation.observers.noObserverBoundTitle')
  })

  it('speaks once the roster is known empty and observations remain', async () => {
    const wrapper = await renderView(ObserverPanel, {
      props: baseProps({
        rosterKnown: true,
        observerAgents: [],
        observations: [makeObservation('o1')],
      }),
    })

    expect(wrapper.text()).toContain('conversation.observers.noObserverBoundTitle')
  })
})
