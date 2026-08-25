// The trailing-edge throttle both worksheet surfaces share ([R32.01]).
//
// Extracted from two near-identical copies in `ActivityHost` and `ActivityPanel`;
// this file pins the contract that extraction has to preserve, including the one
// case a `pending !== null` guard silently changed.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import {
  DRAFT_THROTTLE_MS,
  useDraftThrottle,
  type UseDraftThrottle,
} from '../composables/useDraftThrottle'

function mountThrottle<T>(): {
  api: UseDraftThrottle<T>
  emitted: T[]
  unmount: () => void
} {
  const emitted: T[] = []
  let api!: UseDraftThrottle<T>
  const Host = defineComponent({
    setup() {
      api = useDraftThrottle<T>((v) => emitted.push(v))
      return () => null
    },
  })
  const wrapper = mount(Host)
  return { api, emitted, unmount: () => wrapper.unmount() }
}

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('useDraftThrottle', () => {
  it('emits once per window, with the most recent value', () => {
    const { api, emitted } = mountThrottle<string>()

    api.report('a')
    api.report('ab')
    api.report('abc')
    expect(emitted).toEqual([])

    vi.advanceTimersByTime(DRAFT_THROTTLE_MS)

    expect(emitted).toEqual(['abc'])
  })

  it('emits a null value rather than swallowing it', () => {
    // `/code-review` finding. `T` is instantiated as `unknown` by the plugin path,
    // where `ctx.draft(null)` is a legal way to say "the worksheet is empty now".
    // A `pending !== null` guard dropped it — leaving the participant's EARLIER
    // text readable to a granted agent for the rest of the 900s TTL, which is the
    // one direction this feature must never fail in.
    const { api, emitted } = mountThrottle<unknown>()

    api.report(null)
    vi.advanceTimersByTime(DRAFT_THROTTLE_MS)

    expect(emitted).toEqual([null])
  })

  it('emits other falsy values too', () => {
    const { api, emitted } = mountThrottle<unknown>()

    api.report('')
    vi.advanceTimersByTime(DRAFT_THROTTLE_MS)
    api.report(0)
    vi.advanceTimersByTime(DRAFT_THROTTLE_MS)

    expect(emitted).toEqual(['', 0])
  })

  it('cancel drops what is pending without emitting it', () => {
    // The ordering the clear paths depend on: without this the timer fires after a
    // retraction and re-reports text that has just been sent.
    const { api, emitted } = mountThrottle<string>()

    api.report('about to send this')
    api.cancel()
    vi.advanceTimersByTime(DRAFT_THROTTLE_MS * 2)

    expect(emitted).toEqual([])
  })

  it('starts a fresh window after a cancel', () => {
    const { api, emitted } = mountThrottle<string>()

    api.report('first')
    api.cancel()
    api.report('second')
    vi.advanceTimersByTime(DRAFT_THROTTLE_MS)

    expect(emitted).toEqual(['second'])
  })

  it('cancels on unmount so no timer outlives its component', () => {
    const { api, emitted, unmount } = mountThrottle<string>()

    api.report('left on the tab')
    unmount()
    vi.advanceTimersByTime(DRAFT_THROTTLE_MS * 2)

    expect(emitted).toEqual([])
  })
})
