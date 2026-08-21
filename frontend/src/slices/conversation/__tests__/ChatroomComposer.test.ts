// T-7 of docs/tasks/2026-08-19-chatroom-scroll-and-composer (F-14).
//
// The composer was written to the spec's 36px/192px pair but nothing ever
// assigned a height, so the box sat at one line and scrolled internally.
//
// jsdom performs no layout, so `scrollHeight` here is a stub the test drives.
// That means these cases pin the formula and its two triggers, NOT the rendered
// line count -- AC-7 is the browser half and cannot be closed from this file.

import { describe, it, expect, beforeEach } from 'vitest'
import { renderView } from '../../../../tests/utils'
import ChatroomComposer from '../components/ChatroomComposer.vue'

// Driven per-test; the getter is installed once on the prototype so it survives
// the composable resetting `style.height = 'auto'` before each measurement.
let fakeScrollHeight = 0

beforeEach(() => {
  fakeScrollHeight = 0
  Object.defineProperty(HTMLTextAreaElement.prototype, 'scrollHeight', {
    configurable: true,
    get: () => fakeScrollHeight,
  })
})

function baseProps(over: Record<string, unknown> = {}) {
  return { modelValue: '', pendingUploads: [], ...over }
}

async function mountComposer(over: Record<string, unknown> = {}) {
  const wrapper = await renderView(ChatroomComposer, { props: baseProps(over) })
  return { wrapper, textarea: wrapper.find('textarea') }
}

describe('ChatroomComposer auto-grow (F-14)', () => {
  it('grows to the content height on input', async () => {
    const { textarea } = await mountComposer()

    fakeScrollHeight = 100
    await textarea.trigger('input')

    expect((textarea.element as HTMLTextAreaElement).style.height).toBe('100px')
  })

  it('stops growing at the 192px maximum and lets the box scroll internally', async () => {
    const { textarea } = await mountComposer()

    fakeScrollHeight = 400
    await textarea.trigger('input')

    expect((textarea.element as HTMLTextAreaElement).style.height).toBe('192px')
  })

  it('shrinks back when a send clears the draft', async () => {
    // The send path clears modelValue without an input event
    // (useChatroomMessages.ts), so the model watch is the only trigger that can
    // return the box to one line. This is the case a keydown-only fix misses.
    const { wrapper, textarea } = await mountComposer({ modelValue: 'a\nb\nc\nd\ne' })

    fakeScrollHeight = 120
    await textarea.trigger('input')
    expect((textarea.element as HTMLTextAreaElement).style.height).toBe('120px')

    fakeScrollHeight = 36
    await wrapper.setProps({ modelValue: '' })

    expect((textarea.element as HTMLTextAreaElement).style.height).toBe('36px')
  })

  it('grows after a programmatic mention insert, which fires no input event', async () => {
    const { wrapper, textarea } = await mountComposer()

    fakeScrollHeight = 72
    await wrapper.setProps({ modelValue: '@reviewer ' })

    expect((textarea.element as HTMLTextAreaElement).style.height).toBe('72px')
  })
})
