// AC-3 / AC-4 — the client half of reporting a room's unsent text (§32).
//
// The composable owns three decisions worth pinning, and none of them is visible
// from the frames alone:
//
// 1. **Trailing-edge throttle.** What matters is the LATEST text; a leading edge
//    would report the first keystroke of a burst and then nothing until the next
//    burst began.
// 2. **A clear cancels anything pending.** Without it the timer fires after the
//    clear and re-reports text the participant has just sent, leaving a "draft"
//    of an already-sent message readable for a full TTL.
// 3. **An emptied composer clears rather than reporting.** Select-all-and-delete
//    is a retraction; an empty entry left in Redis reads as "still composing".

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import {
  DRAFT_THROTTLE_MS,
  useDraftReporting,
  type UseDraftReporting,
} from '../composables/useDraftReporting'

function mountReporter(): {
  api: UseDraftReporting
  frames: Array<Record<string, unknown>>
  unmount: () => void
} {
  const frames: Array<Record<string, unknown>> = []
  let api!: UseDraftReporting
  const Host = defineComponent({
    setup() {
      api = useDraftReporting((frame) => frames.push(frame))
      return () => null
    },
  })
  const wrapper = mount(Host)
  return { api, frames, unmount: () => wrapper.unmount() }
}

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('useDraftReporting — the composer', () => {
  it('sends one frame per burst, carrying the latest text', () => {
    const { api, frames } = mountReporter()

    api.reportComposer('h')
    api.reportComposer('he')
    api.reportComposer('hel')
    expect(frames).toHaveLength(0)

    vi.advanceTimersByTime(DRAFT_THROTTLE_MS)

    expect(frames).toEqual([
      { type: 'draft.update', surface: 'composer', content: 'hel' },
    ])
  })

  it('starts a fresh window after one closes', () => {
    const { api, frames } = mountReporter()

    api.reportComposer('first')
    vi.advanceTimersByTime(DRAFT_THROTTLE_MS)
    api.reportComposer('second')
    vi.advanceTimersByTime(DRAFT_THROTTLE_MS)

    expect(frames.map((f) => f.content)).toEqual(['first', 'second'])
  })

  it('clears rather than reporting when the composer is emptied', () => {
    // A retraction, not a draft. And immediate rather than throttled: the point
    // of emptying the box is that what was there is gone.
    const { api, frames } = mountReporter()

    api.reportComposer('')

    expect(frames).toEqual([{ type: 'draft.clear', surface: 'composer' }])
  })

  it('treats whitespace-only as empty', () => {
    const { api, frames } = mountReporter()

    api.reportComposer('   \n  ')

    expect(frames[0]!.type).toBe('draft.clear')
  })

  it('cancels a pending report when cleared, so a sent message is not re-reported', () => {
    // The regression this file exists for.
    const { api, frames } = mountReporter()

    api.reportComposer('about to send this')
    api.clearComposer()
    vi.advanceTimersByTime(DRAFT_THROTTLE_MS * 2)

    expect(frames).toEqual([{ type: 'draft.clear', surface: 'composer' }])
  })

  it('clears on unmount and leaves no timer behind', () => {
    const { api, frames, unmount } = mountReporter()
    api.reportComposer('left on the tab')

    unmount()
    vi.advanceTimersByTime(DRAFT_THROTTLE_MS * 2)

    expect(frames).toEqual([{ type: 'draft.clear', surface: 'composer' }])
  })
})

describe('useDraftReporting — activity worksheets', () => {
  it('passes an activity report straight through, keyed by its type', () => {
    // The host already throttles this one (`ActivityHost` / `ActivityPanel`), so
    // throttling again here would double the latency between a keystroke and the
    // entry an agent can read, for no reduction in frames.
    const { api, frames } = mountReporter()

    api.reportActivity('mandala-9grid', { cell1: 'home' })

    expect(frames).toEqual([
      {
        type: 'draft.update',
        surface: 'activity',
        key: 'mandala-9grid',
        content: '{"cell1":"home"}',
      },
    ])
  })

  it('clears one worksheet by key', () => {
    const { api, frames } = mountReporter()

    api.clearActivity('mandala-9grid')

    expect(frames).toEqual([
      { type: 'draft.clear', surface: 'activity', key: 'mandala-9grid' },
    ])
  })

  it('passes a string payload through unchanged', () => {
    const { api, frames } = mountReporter()

    api.reportActivity('k', 'already text')

    expect(frames[0]!.content).toBe('already text')
  })

  it('costs the report, not the socket, when a payload will not serialise', () => {
    // A third-party plugin can hand the host a cyclic structure. That must cost
    // one report rather than throwing inside a socket send.
    const cyclic: Record<string, unknown> = {}
    cyclic.self = cyclic
    const { api, frames } = mountReporter()

    expect(() => api.reportActivity('k', cyclic)).not.toThrow()
    expect(frames[0]!.content).toBe('')
  })

  it('never mixes the two surfaces in one frame', () => {
    // The server keys storage on (room, user, surface, key), so a composer frame
    // carrying a key — or an activity frame without one — would be dropped or,
    // worse, land in the wrong entry.
    const { api, frames } = mountReporter()

    api.reportComposer('chat')
    vi.advanceTimersByTime(DRAFT_THROTTLE_MS)
    api.reportActivity('k', {})

    expect(frames[0]).not.toHaveProperty('key')
    expect(frames[1]).toHaveProperty('key', 'k')
  })
})
