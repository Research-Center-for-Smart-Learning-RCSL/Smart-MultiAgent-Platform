import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import ObserverPanel from '../components/ObserverPanel.vue'
import type { ObserverEntry } from '../composables/useObservations'
import type { Observation } from '../types'

function baseProps(
  overrides: Partial<{ observerAgents: ObserverEntry[]; observations: Observation[] }> = {},
) {
  return {
    observerAgents: [] as ObserverEntry[],
    observations: [] as Observation[],
    loading: false,
    hasMore: false,
    loadingMore: false,
    agentNames: { a1: 'Watcher' },
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
