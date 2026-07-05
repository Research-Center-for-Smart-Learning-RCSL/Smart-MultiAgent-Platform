import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SCard from '../SCard.vue'

describe('SCard', () => {
  it('renders default variant and padding without props', () => {
    const wrapper = mount(SCard, { slots: { default: 'body' } })
    expect(wrapper.classes()).toContain('s-card--default')
    expect(wrapper.classes()).toContain('s-card--pad-md')
    expect(wrapper.classes()).not.toContain('s-card--hoverable')
    expect(wrapper.text()).toContain('body')
  })

  it('applies variant, padding, and hoverable classes', () => {
    const wrapper = mount(SCard, {
      props: { variant: 'elevated', padding: 'lg', hoverable: true },
    })
    expect(wrapper.classes()).toContain('s-card--elevated')
    expect(wrapper.classes()).toContain('s-card--pad-lg')
    expect(wrapper.classes()).toContain('s-card--hoverable')
  })

  it('renders header and footer sections only when the slots are provided', () => {
    const bare = mount(SCard)
    expect(bare.find('.s-card__header').exists()).toBe(false)
    expect(bare.find('.s-card__footer').exists()).toBe(false)

    const full = mount(SCard, {
      slots: { header: 'Head', default: 'Body', footer: 'Foot' },
    })
    expect(full.find('.s-card__header').text()).toBe('Head')
    expect(full.find('.s-card__footer').text()).toBe('Foot')
  })
})
