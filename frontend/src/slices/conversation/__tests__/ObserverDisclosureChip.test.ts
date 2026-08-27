import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import ObserverDisclosureChip from '../components/ObserverDisclosureChip.vue'

describe('ObserverDisclosureChip', () => {
  // The chip sits in the 48px header row at the very top of .chatroom, whose
  // `overflow: hidden` clips anything above it — a top-placed tooltip (the
  // STooltip default) is cut off entirely, so the chip must open downward.
  it('opens its tooltip below the chip, not above', async () => {
    // STooltip teleports the bubble to body in production, but renderView
    // stubs Teleport, so the bubble stays inside the wrapper here.
    const wrapper = await renderView(ObserverDisclosureChip)
    const tooltip = wrapper.find('[role="tooltip"]')
    expect(tooltip.exists()).toBe(true)
    expect(tooltip.classes()).toContain('s-tooltip--bottom')
  })
})
