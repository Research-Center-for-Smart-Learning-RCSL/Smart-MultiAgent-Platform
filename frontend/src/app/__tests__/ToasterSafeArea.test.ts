import { mount } from '@vue/test-utils'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { Toaster } from 'vue-sonner'

import { toasterProps } from '../toasterProps'

// mobileViewportContract's T-2 proves the offsets are WRITTEN. These two prove
// the parts a source scan cannot reach.
describe('toaster safe-area offsets', () => {
  const EDGES = ['top', 'right', 'bottom', 'left'] as const
  const DESKTOP_FLOOR = '24px'
  const MOBILE_FLOOR = '16px'

  // §9 named this the riskiest edit in the change: these offsets REPLACE a
  // library default rather than adding to a value this repo controls, so on a
  // device reporting no inset the floors are the only thing keeping the
  // geometry identical to stock. Asserting the props against themselves would
  // stay green through a vue-sonner bump that moved either default - which is
  // precisely the silent drift being guarded against - so the constants are
  // read out of the installed package.
  it('floors each offset at vue-sonner’s own viewport offset', () => {
    // Read by path, not require.resolve: vue-sonner's `exports` map declares
    // only an `import` condition, so CJS resolution of the package throws.
    // `import.meta.url` is not a file URL under the jsdom environment either,
    // so resolve from the run root the way mobileViewportContract does. A
    // layout change makes this throw, which is the right failure - the guard
    // going quiet is the thing that must not happen.
    const lib = readFileSync(
      resolve(process.cwd(), 'node_modules/vue-sonner/lib/index.js'),
      'utf8',
    )

    const constant = (name: string): string => {
      const found = lib.match(new RegExp(`${name}\\s*=\\s*["']([^"']+)["']`))
      expect(found, `${name} not found in the installed vue-sonner`).not.toBeNull()
      return found![1]
    }

    expect(constant('VIEWPORT_OFFSET')).toBe(DESKTOP_FLOOR)
    expect(constant('MOBILE_VIEWPORT_OFFSET')).toBe(MOBILE_FLOOR)
  })

  // `offset` and `mobileOffset` are library props: an upgrade that renamed or
  // dropped either would leave the source scan green while the toasts went back
  // to sitting behind the cutout.
  it('reaches the mounted container as CSS custom properties', () => {
    const wrapper = mount(Toaster, { props: toasterProps((key) => key) })
    // The offsets land on the toast list, not the labelled section wrapper.
    const style = wrapper.get('[data-sonner-toaster]').attributes('style') ?? ''

    for (const edge of EDGES) {
      expect(style).toContain(
        `--offset-${edge}: max(${DESKTOP_FLOOR}, env(safe-area-inset-${edge}, 0px))`,
      )
      expect(style).toContain(
        `--mobile-offset-${edge}: max(${MOBILE_FLOOR}, env(safe-area-inset-${edge}, 0px))`,
      )
    }
  })
})
