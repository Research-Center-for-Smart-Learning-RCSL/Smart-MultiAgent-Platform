import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderView } from '../../../tests/utils'
import ParticleField from '../components/ParticleField.vue'

// jsdom has no 2D canvas context (stubbed to null here, matching what a
// reduced-capability embed returns), so the component takes its inert path:
// the canvas renders but no engine or observers attach.
describe('ParticleField', () => {
  beforeEach(() => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders a decorative canvas hidden from assistive tech', async () => {
    const wrapper = await renderView(ParticleField)
    const canvas = wrapper.find('canvas.particle-field')
    expect(canvas.exists()).toBe(true)
    expect(canvas.attributes('aria-hidden')).toBe('true')
  })

  it('mounts and unmounts cleanly without a 2D context', async () => {
    const wrapper = await renderView(ParticleField)
    expect(() => wrapper.unmount()).not.toThrow()
  })
})
