import { describe, it, expect } from 'vitest'

// The `common.*` namespace is referenced from three slices and belongs to none
// of them, so it lives in the shared bundle alongside the other strings the ui
// atoms use (see the registration comment in `app/main.ts`).
//
// Every call site passes a literal English default -- `t('common.close', 'Close')`
// -- which is a chunk-load safety net and also why a missing namespace is
// invisible: vue-i18n renders the default, the `missing` handler never returns a
// raw key, nothing warns and nothing throws. A zh-TW user just sees English. No
// other gate can see this, so these assertions are the gate.
import en from '../locales/en.json'
import zhTW from '../locales/zh-TW.json'

const REQUIRED = ['close', 'edit', 'delete', 'cancel', 'save'] as const

// `common` is optional on purpose. Typing it as required would turn a deleted
// namespace into a compile error in this file, and a test that cannot run is not
// a test that fails -- the assertions below have to be the thing that breaks.
type Bundle = { common?: Record<string, string> }

const enBundle = en as Bundle
const zhBundle = zhTW as Bundle
const bundles: [string, Bundle][] = [
  ['en', enBundle],
  ['zh-TW', zhBundle],
]

describe('shared locales: the common namespace', () => {
  for (const [name, bundle] of bundles) {
    it(`${name} carries every key the call sites ask for`, () => {
      expect(Object.keys(bundle.common ?? {}).sort()).toEqual([...REQUIRED].sort())
    })

    it(`${name} translates each key to a non-empty string`, () => {
      for (const key of REQUIRED) {
        expect(typeof bundle.common?.[key]).toBe('string')
        expect(bundle.common?.[key]).not.toBe('')
      }
    })
  }

  it('en and zh-TW declare identical key sets', () => {
    // A key present in one bundle only is the same failure in a narrower form:
    // the locale that lacks it silently renders the English default.
    expect(Object.keys(enBundle.common ?? {}).sort()).toEqual(Object.keys(zhBundle.common ?? {}).sort())
  })
})

// The guard against recurrence: adding a sixth verb at a call site without adding
// it to the bundles reproduces this defect exactly, and just as invisibly.
const sources = import.meta.glob(['/src/**/*.vue', '/src/**/*.ts', '!/src/**/__tests__/**'], {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

const REFERENCE_RE = /\$?t\(\s*['"]common\.([A-Za-z0-9_]+)['"]/g

describe('shared locales: every referenced common key resolves', () => {
  const referenced = new Map<string, string[]>()
  for (const [file, source] of Object.entries(sources)) {
    for (const match of source.matchAll(REFERENCE_RE)) {
      const key = match[1]
      referenced.set(key, [...(referenced.get(key) ?? []), file])
    }
  }

  it('finds the call sites at all, so a broken scan cannot pass vacuously', () => {
    expect(referenced.size).toBeGreaterThan(0)
  })

  it('has every referenced key in both bundles', () => {
    const missing = [...referenced.entries()]
      .filter(([key]) => !(key in (enBundle.common ?? {})) || !(key in (zhBundle.common ?? {})))
      .map(([key, files]) => `${key} (${files.join(', ')})`)
    expect(missing).toEqual([])
  })
})
