import { describe, expect, it } from 'vitest'
import { defineComponent, markRaw } from 'vue'
import { mount } from '@vue/test-utils'
import SStatTile from '../SStatTile.vue'

const TestIcon = defineComponent({ template: '<svg />' })

describe('SStatTile', () => {
  it('renders the label, value, hint, and optional icon', () => {
    const wrapper = mount(SStatTile, {
      props: { label: 'Active rooms', value: 12, hint: '10 valid', icon: markRaw(TestIcon) },
    })

    expect(wrapper.text()).toContain('Active rooms')
    expect(wrapper.text()).toContain('12')
    expect(wrapper.text()).toContain('10 valid')
    expect(wrapper.find('svg.s-stat-tile__icon').exists()).toBe(true)
  })

  it('applies tone and compact classes and renders custom value content', () => {
    const wrapper = mount(SStatTile, {
      props: { label: 'Errors', value: 2, tone: 'danger', compact: true },
      slots: { value: '<span data-testid="custom-value">2 errors</span>' },
    })

    expect(wrapper.classes()).toContain('s-stat-tile--danger')
    expect(wrapper.classes()).toContain('s-stat-tile--compact')
    expect(wrapper.find('[data-testid="custom-value"]').text()).toBe('2 errors')
  })
})
