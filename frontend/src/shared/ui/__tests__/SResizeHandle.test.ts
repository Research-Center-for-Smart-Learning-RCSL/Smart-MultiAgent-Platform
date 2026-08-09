import { describe, it, expect, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import SResizeHandle from '../SResizeHandle.vue'

const BASE = { value: 300, min: 200, max: 720, label: 'Resize the side panel' }

function mountHandle(props: Record<string, unknown> = {}) {
  return mount(SResizeHandle, { props: { ...BASE, ...props }, attachTo: document.body })
}

function emitted(wrapper: ReturnType<typeof mountHandle>): number[] {
  return (wrapper.emitted('update:value') ?? []).map((args) => (args as [number])[0])
}

describe('SResizeHandle', () => {
  afterEach(() => {
    document.body.style.userSelect = ''
    document.body.style.cursor = ''
  })

  // AC-9
  it('exposes the separator role and its current bounds', () => {
    const el = mountHandle().find('[role="separator"]')
    expect(el.exists()).toBe(true)
    expect(el.attributes('aria-orientation')).toBe('vertical')
    expect(el.attributes('aria-valuenow')).toBe('300')
    expect(el.attributes('aria-valuemin')).toBe('200')
    expect(el.attributes('aria-valuemax')).toBe('720')
    expect(el.attributes('aria-label')).toBe('Resize the side panel')
    // Keyboard reachability comes from the element being a real button rather
    // than from a tabindex on a static node.
    expect(el.element.tagName).toBe('BUTTON')
    expect(el.attributes('disabled')).toBeUndefined()
  })

  it('steps with the arrow keys', async () => {
    const wrapper = mountHandle()
    await wrapper.trigger('keydown', { key: 'ArrowRight' })
    await wrapper.trigger('keydown', { key: 'ArrowLeft' })
    expect(emitted(wrapper)).toEqual([316, 284])
  })

  it('reverses the arrow keys when inverted', async () => {
    const wrapper = mountHandle({ invert: true })
    await wrapper.trigger('keydown', { key: 'ArrowRight' })
    await wrapper.trigger('keydown', { key: 'ArrowLeft' })
    expect(emitted(wrapper)).toEqual([284, 316])
  })

  it('takes a larger step with shift held', async () => {
    const wrapper = mountHandle()
    await wrapper.trigger('keydown', { key: 'ArrowRight', shiftKey: true })
    expect(emitted(wrapper)).toEqual([364])
  })

  it('jumps to the bounds with Home and End', async () => {
    const wrapper = mountHandle()
    await wrapper.trigger('keydown', { key: 'Home' })
    await wrapper.trigger('keydown', { key: 'End' })
    expect(emitted(wrapper)).toEqual([200, 720])
  })

  it('jumps to the mirrored bounds when inverted', async () => {
    const wrapper = mountHandle({ invert: true })
    await wrapper.trigger('keydown', { key: 'Home' })
    await wrapper.trigger('keydown', { key: 'End' })
    expect(emitted(wrapper)).toEqual([720, 200])
  })

  it('asks for a reset on Enter', async () => {
    const wrapper = mountHandle()
    await wrapper.trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('reset')).toHaveLength(1)
    expect(wrapper.emitted('update:value')).toBeUndefined()
  })

  it('leaves unhandled keys alone', async () => {
    const wrapper = mountHandle()
    await wrapper.trigger('keydown', { key: 'a' })
    await wrapper.trigger('keydown', { key: 'ArrowUp' })
    expect(wrapper.emitted('update:value')).toBeUndefined()
  })

  // Resolving against the drag-start value rather than accumulating per move is
  // what keeps the handle under the pointer after a drag past a bound.
  it('reports widths measured from where the drag started', async () => {
    const wrapper = mountHandle({ invert: true })
    await wrapper.trigger('pointerdown', { button: 0, clientX: 500, pointerId: 1 })
    await wrapper.trigger('pointermove', { clientX: 480, pointerId: 1 })
    await wrapper.trigger('pointermove', { clientX: 450, pointerId: 1 })
    expect(emitted(wrapper)).toEqual([320, 350])
  })

  // preventDefault on pointerdown suppresses click-focus, so grabbing the handle
  // and then fine-tuning with the arrow keys would otherwise be impossible.
  it('takes focus when grabbed so the keyboard can take over', async () => {
    const wrapper = mountHandle()
    await wrapper.trigger('pointerdown', { button: 0, clientX: 500, pointerId: 1 })
    expect(document.activeElement).toBe(wrapper.element)
  })

  it('ignores pointer movement that is not part of a drag', async () => {
    const wrapper = mountHandle()
    await wrapper.trigger('pointermove', { clientX: 480, pointerId: 1 })
    expect(wrapper.emitted('update:value')).toBeUndefined()
  })

  it('ignores a non-primary button', async () => {
    const wrapper = mountHandle()
    await wrapper.trigger('pointerdown', { button: 2, clientX: 500, pointerId: 1 })
    await wrapper.trigger('pointermove', { clientX: 480, pointerId: 1 })
    expect(wrapper.emitted('update:value')).toBeUndefined()
  })

  // AC-11
  it('restores text selection when the drag is cancelled', async () => {
    const wrapper = mountHandle()
    await wrapper.trigger('pointerdown', { button: 0, clientX: 500, pointerId: 1 })
    expect(document.body.style.userSelect).toBe('none')

    await wrapper.trigger('pointercancel', { pointerId: 1 })
    expect(document.body.style.userSelect).toBe('')
    expect(document.body.style.cursor).toBe('')

    await wrapper.trigger('pointermove', { clientX: 400, pointerId: 1 })
    expect(wrapper.emitted('update:value')).toBeUndefined()
  })

  // The browser can revoke capture without a pointerup or pointercancel, which
  // would otherwise strand the whole page unselectable under a resize cursor.
  it('ends the drag when the browser revokes pointer capture', async () => {
    const wrapper = mountHandle()
    await wrapper.trigger('pointerdown', { button: 0, clientX: 500, pointerId: 1 })
    await wrapper.trigger('lostpointercapture', { pointerId: 1 })

    expect(document.body.style.userSelect).toBe('')
    expect(document.body.style.cursor).toBe('')
    await wrapper.trigger('pointermove', { clientX: 400, pointerId: 1 })
    expect(wrapper.emitted('update:value')).toBeUndefined()
  })

  it('restores text selection when unmounted mid-drag', async () => {
    const wrapper = mountHandle()
    await wrapper.trigger('pointerdown', { button: 0, clientX: 500, pointerId: 1 })
    wrapper.unmount()
    expect(document.body.style.userSelect).toBe('')
  })
})
