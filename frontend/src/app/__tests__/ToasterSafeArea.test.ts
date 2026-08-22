import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { Toaster } from 'vue-sonner'

import { toasterProps } from '../toasterProps'

// mobileViewportContract's T-2 proves the offsets are WRITTEN. This proves
// vue-sonner still reads them: `offset`/`mobileOffset` are library props, and an
// upgrade that renames or drops either would leave the source scan green while
// the toasts went back to sitting behind the cutout.
//
// It is also the guard on §9's riskiest edit - these replace a library default
// rather than adding to a value this repo controls, so the floors have to stay
// byte-identical to vue-sonner's VIEWPORT_OFFSET / MOBILE_VIEWPORT_OFFSET.
describe('toaster safe-area offsets', () => {
  const EDGES = ['top', 'right', 'bottom', 'left'] as const

  it('reaches the mounted container as CSS custom properties', () => {
    const wrapper = mount(Toaster, { props: toasterProps((key) => key) })
    // The offsets land on the toast list, not the labelled section wrapper.
    const style = wrapper.get('[data-sonner-toaster]').attributes('style') ?? ''

    for (const edge of EDGES) {
      expect(style).toContain(`--offset-${edge}: max(24px, env(safe-area-inset-${edge}, 0px))`)
      expect(style).toContain(
        `--mobile-offset-${edge}: max(16px, env(safe-area-inset-${edge}, 0px))`,
      )
    }
  })
})
