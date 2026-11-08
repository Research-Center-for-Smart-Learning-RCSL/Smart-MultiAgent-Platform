// Creativity columns on the watchlist (research data integrity spec, section 5).
// The server fills `creativity` only for an owner or admin, so the columns must
// appear only when some entry carries it, and a missing mean shows a dash whose
// tooltip says why.

import { describe, expect, it } from 'vitest'

import { renderView } from '../../../../tests/utils'
import StudentWatchlist from '../components/StudentWatchlist.vue'
import type { WatchlistEntry } from '../types'

const ROOM = 'r1'

function entry(over: Partial<WatchlistEntry> = {}): WatchlistEntry {
  return {
    room_id: ROOM,
    kind: 'user',
    display_name: 'Lin',
    subject_code: 'u:1',
    valid_count: 1,
    last_valid_submission_at: null,
    needs_attention: false,
    reason: null,
    ...over,
  }
}

async function mount(entries: WatchlistEntry[]) {
  return renderView(StudentWatchlist, {
    props: { entries, rooms: [{ room_id: ROOM, room_name: 'Room 1' }], idleMinutes: 10 },
  })
}

describe('StudentWatchlist creativity columns', () => {
  it('shows no creativity column when no entry carries scores', async () => {
    const wrapper = await mount([entry()])
    expect(wrapper.text()).not.toContain('dashboard.creativity.originality')
  })

  it('shows each mean to one decimal', async () => {
    const wrapper = await mount([
      entry({
        creativity: {
          scored_rounds: 2,
          fluency: 3,
          flexibility: 2.25,
          originality: 1.5,
          convergence: 4,
          last_status: 'scored',
          last_reason: null,
        },
      }),
    ])
    expect(wrapper.text()).toContain('dashboard.creativity.originality')
    expect(wrapper.find('[data-testid="creativity-originality"]').text()).toBe('1.5')
    expect(wrapper.find('[data-testid="creativity-flexibility"]').text()).toBe('2.3')
  })

  it('shows a dash for a participant whose scoring failed', async () => {
    const wrapper = await mount([
      entry({
        creativity: {
          scored_rounds: 0,
          fluency: null,
          flexibility: null,
          originality: null,
          convergence: null,
          last_status: 'error',
          last_reason: 'judge_bad_output',
        },
      }),
      entry({ subject_code: 'u:2', display_name: 'Chen' }),
    ])
    expect(wrapper.find('[data-testid="creativity-originality"]').exists()).toBe(false)
    expect(wrapper.findAll('[data-testid="creativity-originality-missing"]')).toHaveLength(2)
  })

  it.each([
    ['error', 'judge_bad_output', 'dashboard.creativity.reason.judge_bad_output'],
    ['skipped', 'judge_cap', 'dashboard.creativity.reason.judge_cap'],
    ['error', null, 'dashboard.creativity.error'],
    ['pending', null, 'dashboard.creativity.pending'],
  ] as const)('says why a %s mean is missing (%s)', async (status, reason, key) => {
    const wrapper = await mount([
      entry({
        creativity: {
          scored_rounds: 0,
          fluency: null,
          flexibility: null,
          originality: null,
          convergence: null,
          last_status: status,
          last_reason: reason,
        },
      }),
    ])
    // The reason is in the cell as text, not only in the hover tooltip.
    expect(wrapper.find('[data-testid="creativity-originality-missing"]').text()).toContain(key)
  })
})