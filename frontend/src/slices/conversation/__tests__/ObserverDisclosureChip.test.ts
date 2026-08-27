import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import ObserverDisclosureChip from '../components/ObserverDisclosureChip.vue'

describe('ObserverDisclosureChip', () => {
  // UX pin, not a clipping fix: the teleported tooltip clips nowhere, but the
  // chip sits in the room header and an upward bubble would float over the
  // app topbar instead of over the room's own content.
  it('opens its tooltip below the chip, not above', async () => {
    // STooltip teleports the bubble to body in production, but renderView
    // stubs Teleport, so the bubble stays inside the wrapper here.
    const wrapper = await renderView(ObserverDisclosureChip)
    const tooltip = wrapper.find('[role="tooltip"]')
    expect(tooltip.exists()).toBe(true)
    expect(tooltip.classes()).toContain('s-tooltip--bottom')
  })
})
