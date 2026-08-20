import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Toaster, toast } from 'vue-sonner'

import { toasterProps } from '../toasterProps'

afterEach(() => toast.dismiss())

// Mounted from the app's own props, not a hand-written set: a locally supplied
// `closeButton: true` would let App.vue drop it and still pass here, which is
// exactly how the localized close label shipped with nothing to label.
describe('vue-sonner accessibility labels', () => {
  it.each([
    ['Notifications', 'Close notification'],
    ['通知', '關閉通知'],
  ])('renders the localized container and close-button labels', async (container, close) => {
    const messages: Record<string, string> = {
      'app.notifications.label': container,
      'app.notifications.close': close,
    }
    const wrapper = mount(Toaster, { props: toasterProps((key) => messages[key] ?? key) })
    toast.success('Saved')
    await flushPromises()

    await vi.waitFor(() => {
      expect(wrapper.get('section[aria-live="polite"]').attributes('aria-label')).toBe(`${container} alt+T`)
      expect(wrapper.get(`button[aria-label="${close}"]`).exists()).toBe(true)
    })
  })
})
