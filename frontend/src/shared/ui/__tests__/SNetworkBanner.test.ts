import { mount } from '@vue/test-utils'
import { ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { i18n } from '@shared/i18n'
import SNetworkBanner from '../SNetworkBanner.vue'

const retryNow = vi.hoisted(() => vi.fn())

vi.mock('@shared/composables', () => ({
  useNetworkStatus: () => ({ online: ref(false), retryNow }),
}))

describe('SNetworkBanner', () => {
  beforeEach(() => {
    i18n.global.mergeLocaleMessage('en', {
      app: { network: { title: 'Connection lost', message: 'Reconnecting', retry: 'Retry' } },
    })
    i18n.global.locale.value = 'en'
  })

  it('adds the top-bar offset only when its host layout requests it', () => {
    const appLayout = mount(SNetworkBanner, {
      props: { belowTopbar: true },
      global: { plugins: [i18n] },
    })
    expect(appLayout.get('.s-net-banner').classes()).toContain('s-net-banner--below-topbar')

    const topLevelLayout = mount(SNetworkBanner, {
      global: { plugins: [i18n] },
    })
    expect(topLevelLayout.get('.s-net-banner').classes()).not.toContain('s-net-banner--below-topbar')
  })
})
