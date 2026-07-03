import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import ObserverPanel from '../components/ObserverPanel.vue'
import type { ObserverEntry } from '../composables/useObservations'

function baseProps(overrides: Partial<{ observerAgents: ObserverEntry[] }> = {}) {
  return {
    observerAgents: [] as ObserverEntry[],
    observations: [],
    loading: false,
    hasMore: false,
    loadingMore: false,
    agentNames: { a1: 'Watcher' },
    ...overrides,
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
