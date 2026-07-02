import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import ObservationCard from '../components/ObservationCard.vue'
import type { Observation } from '../types'

function observation(over: Partial<Observation> = {}): Observation {
  return {
    id: 'o1',
    chatroom_id: 'c1',
    agent_id: 'a1',
    content_md: 'the analysis',
    metadata: {},
    trigger: 'every_n_messages',
    trigger_message_id: null,
    released_at: null,
    release_target: null,
    released_by_user_id: null,
    created_at: '2026-01-01T00:00:00Z',
    ...over,
  }
}

describe('ObservationCard', () => {
  it('renders the analysis body through the markdown pipeline', async () => {
    const wrapper = await renderView(ObservationCard, {
      props: { observation: observation(), agentName: 'Analyst-A' },
    })
    expect(wrapper.text()).toContain('the analysis')
    expect(wrapper.text()).toContain('Analyst-A')
  })

  // The test i18n harness echoes keys (bundles load lazily), so assert on
  // structure and the exact key rather than translated copy.
  it('shows a Release action while unreleased and hides it once released', async () => {
    const unreleased = await renderView(ObservationCard, {
      props: { observation: observation(), agentName: 'A' },
    })
    const unreleasedButtons = unreleased.findAll('button').map((b) => b.text())
    expect(unreleasedButtons).toContain('conversation.observers.release')
    expect(unreleased.find('.obs-card__chip').exists()).toBe(false)

    const released = await renderView(ObservationCard, {
      props: {
        observation: observation({
          released_at: '2026-01-02T00:00:00Z',
          release_target: { kind: 'room', message_id: 'm9' },
        }),
        agentName: 'A',
      },
    })
    // The release-state chip appears; the primary Release button does not.
    expect(released.find('.obs-card__chip').text()).toContain('conversation.observers.releasedToRoom')
    const releasedButtons = released.findAll('button').map((b) => b.text())
    expect(releasedButtons).not.toContain('conversation.observers.release')
  })

  it('marks a private release with the woken state chip', async () => {
    const wrapper = await renderView(ObservationCard, {
      props: {
        observation: observation({
          released_at: '2026-01-02T00:00:00Z',
          release_target: { kind: 'agents', agent_ids: ['x', 'y'], woken: true },
        }),
        agentName: 'A',
      },
    })
    const chip = wrapper.find('.obs-card__chip').text()
    expect(chip).toContain('conversation.observers.releasedToAgents')
    expect(chip).toContain('conversation.observers.andWoken')
  })
})
