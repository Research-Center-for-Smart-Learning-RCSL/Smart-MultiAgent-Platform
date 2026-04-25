import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import InstructChainView from '../InstructChainView.vue'

describe('InstructChainView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(InstructChainView)
    expect(wrapper.exists()).toBe(true)
  })
})
