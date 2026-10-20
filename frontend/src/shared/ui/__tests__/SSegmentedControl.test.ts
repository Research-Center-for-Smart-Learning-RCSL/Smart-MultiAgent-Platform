import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import SSegmentedControl from '../SSegmentedControl.vue'

const options = [
  { value: '1h', label: '1h', testId: 'window-1h' },
  { value: '24h', label: '24h', testId: 'window-24h' },
  { value: '7d', label: '7d', disabled: true, title: 'Unavailable' },
]

describe('SSegmentedControl', () => {
  it('renders an accessible group and marks the selected option', () => {
    const wrapper = mount(SSegmentedControl, {
      props: { modelValue: '1h', options, accessibleLabel: 'Time window' },
    })

    expect(wrapper.get('[role="group"]').attributes('aria-label')).toBe('Time window')
    expect(wrapper.get('[data-testid="window-1h"]').attributes('aria-pressed')).toBe('true')
    expect(wrapper.get('[data-testid="window-24h"]').attributes('aria-pressed')).toBe('false')
    expect(wrapper.get('button[title="Unavailable"]').attributes('disabled')).toBeDefined()
  })

  it('emits the selected value and ignores disabled options', async () => {
    const wrapper = mount(SSegmentedControl, {
      props: { modelValue: '1h', options, accessibleLabel: 'Time window' },
    })

    await wrapper.get('[data-testid="window-24h"]').trigger('click')
    await wrapper.get('button[title="Unavailable"]').trigger('click')

    expect(wrapper.emitted('update:modelValue')).toEqual([['24h']])
  })
})
