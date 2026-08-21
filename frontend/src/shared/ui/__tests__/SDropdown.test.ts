import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'

import { declaration, readComponentStyles, topLevelRule } from '../../../../tests/utils'
import SDropdown from '../SDropdown.vue'

const VIEWPORT_HEIGHT = 768
const VIEWPORT_MARGIN = 8

const items = [
  { key: 'a', label: 'Alpha' },
  { key: 'b', label: 'Beta' },
  { key: 'c', label: 'Gamma' },
]

function rect(top: number, height: number): DOMRect {
  return {
    top, bottom: top + height, height, left: 100, right: 220, width: 120,
    x: 100, y: top, toJSON: () => ({}),
  } as DOMRect
}

/**
 * jsdom returns zeros from every geometry read, so the flip and the cap have
 * nothing to decide from. Drive both off the element's own class: the trigger
 * reports where it sits, the menu reports its natural (uncapped) height.
 */
function stubGeometry(
  triggerTop: number,
  triggerHeight: number,
  menuHeight: number,
  viewportHeight = VIEWPORT_HEIGHT,
): void {
  window.innerHeight = viewportHeight
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(
    function (this: HTMLElement) {
      return this.classList.contains('s-dropdown__trigger')
        ? rect(triggerTop, triggerHeight)
        : rect(0, 0)
    },
  )
  Object.defineProperty(HTMLElement.prototype, 'scrollHeight', {
    configurable: true,
    get(this: HTMLElement) {
      return this.classList.contains('s-dropdown__menu') ? menuHeight : 0
    },
  })
}

async function openDropdown(): Promise<VueWrapper> {
  const wrapper = mount(SDropdown, {
    props: { items },
    slots: { trigger: '<button type="button">Open</button>' },
    global: { stubs: { teleport: true } },
  })
  await wrapper.get('.s-dropdown__trigger').trigger('click')
  await wrapper.vm.$nextTick()
  await wrapper.vm.$nextTick()
  return wrapper
}

function menuStyle(wrapper: VueWrapper): Record<string, string> {
  const style = wrapper.get('.s-dropdown__menu').attributes('style') ?? ''
  return Object.fromEntries(
    style.split(';').filter(Boolean).map((d) => {
      const [prop, ...rest] = d.split(':')
      return [prop?.trim() ?? '', rest.join(':').trim()]
    }),
  )
}

describe('SDropdown viewport fit', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    delete (HTMLElement.prototype as unknown as Record<string, unknown>)['scrollHeight']
  })

  // F-9: updateMenuPosition read the trigger's rect alone - no viewport height,
  // no menu size - so a menu opened near the bottom rendered past it, and the
  // last items were unreachable because the content region was already at its
  // scroll end.
  it('flips upward when the menu does not fit below and there is more room above', async () => {
    stubGeometry(600, 32, 300)
    const style = menuStyle(await openDropdown())

    expect(style['bottom']).toBeDefined()
    expect(style['top']).toBeUndefined()
  })

  it('stays below the trigger when the menu fits there', async () => {
    stubGeometry(100, 32, 120)
    const style = menuStyle(await openDropdown())

    expect(style['top']).toBe('136px')
    expect(style['bottom']).toBeUndefined()
  })

  // Flip alone cannot help a menu taller than both sides; a cap alone would
  // show two items with a scrollbar where a flip would have shown all of them.
  it('caps the height to the chosen side when the menu fits on neither', async () => {
    stubGeometry(340, 32, 4000)
    const style = menuStyle(await openDropdown())

    const maxHeight = Number.parseInt(style['maxHeight'] ?? style['max-height'] ?? '', 10)
    expect(Number.isNaN(maxHeight)).toBe(false)
    expect(maxHeight).toBeLessThanOrEqual(VIEWPORT_HEIGHT)
    expect(maxHeight).toBeGreaterThan(0)
  })

  // The height cap has a floor, so a menu is never capped to nothing. That
  // floor must not push the box off screen, which would reintroduce exactly
  // the unreachable items F-9 is about: on a viewport too short for either
  // side, the menu pins to the viewport rather than to the trigger.
  it.each([
    ['below the trigger', 60],
    ['flipped above the trigger', 90],
  ])('keeps the whole menu on screen when the floor exceeds the room %s', async (_case, top) => {
    const viewportHeight = 160
    stubGeometry(top, 32, 600, viewportHeight)
    const style = menuStyle(await openDropdown())

    const maxHeight = Number.parseInt(style['maxHeight'] ?? style['max-height'] ?? '', 10)
    expect(maxHeight).toBeLessThanOrEqual(viewportHeight - VIEWPORT_MARGIN * 2)

    // Resolve the box's top edge from whichever edge was written.
    const boxTop = style['top'] !== undefined
      ? Number.parseInt(style['top'], 10)
      : viewportHeight - Number.parseInt(style['bottom'] ?? '0', 10) - maxHeight
    expect(boxTop).toBeGreaterThanOrEqual(0)
    expect(boxTop + maxHeight).toBeLessThanOrEqual(viewportHeight)
  })

  // The second, independent half of F-9: updateMenuPosition ran before
  // `await nextTick()`, so the menu did not exist and could never be measured.
  // A zero-height menu would neither flip nor cap.
  it('positions the menu only once it exists to be measured', async () => {
    stubGeometry(600, 32, 300)
    const wrapper = mount(SDropdown, {
      props: { items },
      slots: { trigger: '<button type="button">Open</button>' },
      global: { stubs: { teleport: true } },
    })

    const menuPresent: boolean[] = []
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(
      function (this: HTMLElement) {
        if (this.classList.contains('s-dropdown__trigger')) {
          menuPresent.push(wrapper.find('.s-dropdown__menu').exists())
          return rect(600, 32)
        }
        return rect(0, 0)
      },
    )

    await wrapper.get('.s-dropdown__trigger').trigger('click')
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    expect(menuPresent.length).toBeGreaterThan(0)
    expect(menuPresent.every(Boolean)).toBe(true)
  })

  it('lets a capped menu scroll its own items', () => {
    const rule = topLevelRule(readComponentStyles('shared/ui/SDropdown.vue'), '.s-dropdown__menu')
    expect(rule).not.toBeNull()
    expect(declaration(rule as string, 'overflow-y')).toBe('auto')
  })
})
