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
    blocks: [],
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

  // ---- presentation blocks ([R28.15]) ------------------------------------- #

  it('renders blocks instead of the markdown body when the turn assembled any', async () => {
    const wrapper = await renderView(ObservationCard, {
      props: {
        observation: observation({
          content_md: 'the serialisation',
          blocks: [
            { kind: 'timeline', basis: 'transcript', entries: [{ label: 'a moment' }] },
          ],
        }),
        agentName: 'A',
      },
    })
    expect(wrapper.text()).toContain('a moment')
    // The markdown body is the release payload, not a second copy in the panel.
    expect(wrapper.text()).not.toContain('the serialisation')
  })

  it('keeps the markdown path byte-identical for an observation with no blocks', async () => {
    // AC-3's rendering half: every row written before this feature takes it.
    const wrapper = await renderView(ObservationCard, {
      props: { observation: observation(), agentName: 'A' },
    })
    expect(wrapper.find('.obs-card__body').html()).toContain('the analysis')
    expect(wrapper.find('.obs-card__prose').exists()).toBe(false)
  })

  it('renders a prose block at any position through the one sanitised binding', async () => {
    const wrapper = await renderView(ObservationCard, {
      props: {
        observation: observation({
          blocks: [
            { kind: 'timeline', basis: 'transcript', entries: [{ label: 'first' }] },
            { kind: 'prose', text: '**bold words**' },
          ],
        }),
        agentName: 'A',
      },
    })
    // Rendered markdown, so the pipeline ran; the emphasis survives, the raw
    // asterisks do not.
    expect(wrapper.find('.obs-card__prose').html()).toContain('<strong>bold words</strong>')
  })

  it('sanitises a prose block, since the model reads participant text', async () => {
    const wrapper = await renderView(ObservationCard, {
      props: {
        observation: observation({
          blocks: [{ kind: 'prose', text: '<img src=x onerror="alert(1)">' }],
        }),
        agentName: 'A',
      },
    })
    expect(wrapper.find('.obs-card__prose').html()).not.toContain('onerror')
  })

  it('clamps on block count as well as character count', async () => {
    // Character count is the wrong measure of height once an observation is a
    // stack of figures: a nine-cell grid is three rows tall and barely a hundred
    // characters.
    const short = { kind: 'prose' as const, text: 'x' }
    const few = await renderView(ObservationCard, {
      props: { observation: observation({ content_md: 'x', blocks: [short] }), agentName: 'A' },
    })
    expect(few.find('.obs-card__expand').exists()).toBe(false)

    const many = await renderView(ObservationCard, {
      props: {
        observation: observation({ content_md: 'x', blocks: [short, short, short, short] }),
        agentName: 'A',
      },
    })
    expect(many.find('.obs-card__expand').exists()).toBe(true)
    expect(many.find('.obs-card__body--clamped').exists()).toBe(true)
  })
})
