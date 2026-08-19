import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Toaster, toast } from 'vue-sonner'

afterEach(() => toast.dismiss())

describe('vue-sonner accessibility labels', () => {
  it.each([
    ['Notifications', 'Close notification'],
    ['通知', '關閉通知'],
  ])('renders the localized container and close-button labels', async (container, close) => {
    const wrapper = mount(Toaster, {
      props: {
        containerAriaLabel: container,
        toastOptions: { closeButton: true, closeButtonAriaLabel: close },
      },
    })
    toast.success('Saved')
    await flushPromises()

    await vi.waitFor(() => {
      expect(wrapper.get('section[aria-live="polite"]').attributes('aria-label')).toBe(`${container} alt+T`)
      expect(wrapper.get(`button[aria-label="${close}"]`).exists()).toBe(true)
    })
  })
})
