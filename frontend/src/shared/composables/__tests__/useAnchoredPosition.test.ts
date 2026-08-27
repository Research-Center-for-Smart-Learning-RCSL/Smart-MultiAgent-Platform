import { afterEach, describe, expect, it } from 'vitest'

import { clampToViewport, isAnchorClippedOut } from '../useAnchoredPosition'

function rect(over: Partial<DOMRect>): DOMRect {
  return {
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    width: 0,
    height: 0,
    x: 0,
    y: 0,
    toJSON: () => ({}),
    ...over,
  } as DOMRect
}

describe('clampToViewport', () => {
  it('keeps an in-range coordinate unchanged', () => {
    expect(clampToViewport(100, 50, 800)).toBe(100)
  })

  it('clamps to the margin on the leading edge', () => {
    expect(clampToViewport(-30, 50, 800)).toBe(8)
  })

  it('clamps so the box stays inside the trailing edge', () => {
    expect(clampToViewport(790, 50, 800)).toBe(742)
  })

  it('lets the leading edge win when the box is larger than the viewport', () => {
    // A negative trailing bound would otherwise push the box's start off
    // screen; the start staying reachable is the invariant.
    expect(clampToViewport(0, 900, 800)).toBe(8)
  })
})

describe('isAnchorClippedOut', () => {
  afterEach(() => {
    document.body.innerHTML = ''
  })

  function anchorIn(parentStyle: Partial<CSSStyleDeclaration>): {
    anchor: HTMLElement
    parent: HTMLElement
  } {
    const parent = document.createElement('div')
    Object.assign(parent.style, parentStyle)
    const anchor = document.createElement('span')
    parent.appendChild(anchor)
    document.body.appendChild(parent)
    return { anchor, parent }
  }

  it('reports "not clipped" for a zero-size rect (layout has not run)', () => {
    // jsdom rects are all-zero; treating that as clipped would dismiss every
    // overlay under test the moment it repositioned.
    const { anchor } = anchorIn({})
    expect(isAnchorClippedOut(anchor)).toBe(false)
  })

  it('detects an anchor scrolled fully above its clipping ancestor', () => {
    const { anchor, parent } = anchorIn({ overflowY: 'auto' })
    anchor.getBoundingClientRect = () =>
      rect({ top: 10, bottom: 30, left: 10, right: 60, width: 50, height: 20 })
    parent.getBoundingClientRect = () =>
      rect({ top: 40, bottom: 400, left: 0, right: 500, width: 500, height: 360 })
    expect(isAnchorClippedOut(anchor)).toBe(true)
  })

  it('accepts an anchor still visible inside its clipping ancestor', () => {
    const { anchor, parent } = anchorIn({ overflowY: 'auto' })
    anchor.getBoundingClientRect = () =>
      rect({ top: 100, bottom: 120, left: 10, right: 60, width: 50, height: 20 })
    parent.getBoundingClientRect = () =>
      rect({ top: 40, bottom: 400, left: 0, right: 500, width: 500, height: 360 })
    expect(isAnchorClippedOut(anchor)).toBe(false)
  })

  it('ignores non-clipping ancestors entirely', () => {
    const { anchor, parent } = anchorIn({})
    anchor.getBoundingClientRect = () =>
      rect({ top: 10, bottom: 30, left: 10, right: 60, width: 50, height: 20 })
    parent.getBoundingClientRect = () =>
      rect({ top: 40, bottom: 400, left: 0, right: 500, width: 500, height: 360 })
    expect(isAnchorClippedOut(anchor)).toBe(false)
  })

  it('detects an anchor scrolled out of the viewport itself', () => {
    const { anchor } = anchorIn({})
    anchor.getBoundingClientRect = () =>
      rect({ top: -50, bottom: -10, left: 10, right: 60, width: 50, height: 40 })
    expect(isAnchorClippedOut(anchor)).toBe(true)
  })
})
