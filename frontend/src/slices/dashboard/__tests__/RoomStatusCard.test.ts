import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import RoomStatusCard from '../components/RoomStatusCard.vue'
import en from '../locales/en.json'
import type { RoomSummary } from '../types'

function room(overrides: Partial<RoomSummary> = {}): RoomSummary {
  return {
    room_id: 'r1',
    room_name: 'Room r1',
    round: null,
    total_submissions: 4,
    valid_count: 3,
    last_valid_submission_at: null,
    status: 'green',
    online_count: 3,
    participant_count: 5,
    message_counts: { human: 7, agent: 2 },
    ...overrides,
  } as RoomSummary
}

function mountCard(summary: RoomSummary) {
  const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })
  return mount(RoomStatusCard, {
    props: { room: summary },
    global: {
      plugins: [i18n],
      stubs: { RouterLink: { template: '<a><slot /></a>' } },
    },
  })
}

describe('RoomStatusCard', () => {
  it('shows the online and participant counts and the human/agent split', () => {
    const text = mountCard(room()).text()
    expect(text).toContain('3 online')
    expect(text).toContain('5 participants')
    expect(text).toContain('7 human')
    expect(text).toContain('2 agent')
  })

  it('renders a dash, not 0, when presence is unavailable', () => {
    const text = mountCard(room({ online_count: null })).text()
    expect(text).toContain('—')
    expect(text).not.toContain('online')
  })
})
