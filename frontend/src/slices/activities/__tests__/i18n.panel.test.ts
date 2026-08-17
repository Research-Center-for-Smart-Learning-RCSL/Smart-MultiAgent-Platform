// AC-10. Two claims about the activity panel's strings, neither of which any
// other gate can make.
//
// The first is that the participant's start/finish buttons are really gone.
// They were removed because their labels collided with the facilitator's --
// zh-TW rendered `startForRoom` as 在聊天室開始 and `join` as 開始, so the panel
// told a participant to wait for the facilitator and then showed them their own
// Start. Re-adding either key is how that comes back, and it would come back
// silently: the component test harness mounts i18n with no bundle at all
// (BOARD.md FU-4), so every component assertion here reads raw keys and a key
// nobody renders looks exactly like a key everybody renders.
//
// The second is bundle parity, which is the same class of invisible defect: a
// key present in one locale only renders as its raw key for the other locale's
// users and warns nowhere.

import { describe, expect, it } from 'vitest'

import en from '../locales/en.json'
import zhTW from '../locales/zh-TW.json'

type Bundle = { activities?: { panel?: Record<string, string> } }

const enPanel = (en as Bundle).activities?.panel ?? {}
const zhPanel = (zhTW as Bundle).activities?.panel ?? {}

/** Keys the participant lifecycle rework retired. */
const RETIRED = ['join', 'finish', 'finishFailed'] as const

const sources = import.meta.glob(['/src/**/*.vue', '/src/**/*.ts', '!/src/**/__tests__/**'], {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

const REFERENCE_RE = /\$?t\(\s*['"]activities\.panel\.([A-Za-z0-9_]+)['"]/g

describe('activity panel locales', () => {
  it('en and zh-TW declare identical key sets', () => {
    expect(Object.keys(enPanel).sort()).toEqual(Object.keys(zhPanel).sort())
  })

  it('translates every key to a non-empty string in both locales', () => {
    for (const [locale, bundle] of [
      ['en', enPanel],
      ['zh-TW', zhPanel],
    ] as const) {
      for (const [key, value] of Object.entries(bundle)) {
        expect(typeof value, `${locale}.${key}`).toBe('string')
        expect(value, `${locale}.${key}`).not.toBe('')
      }
    }
  })

  it('carries none of the retired participant lifecycle keys', () => {
    for (const key of RETIRED) {
      expect(enPanel, `en.${key}`).not.toHaveProperty(key)
      expect(zhPanel, `zh-TW.${key}`).not.toHaveProperty(key)
    }
  })

  it('gives the facilitator and the participant distinct labels in both locales', () => {
    // The defect in words: two buttons, different meanings, one label. Asserted
    // per locale because zh-TW is where the collision actually was.
    for (const bundle of [enPanel, zhPanel]) {
      const labels = [bundle.startForRoom, bundle.markDone, bundle.markDoneUndo, bundle.end]
      expect(new Set(labels).size).toBe(labels.length)
    }
  })
})

describe('activity panel locales: every referenced key resolves', () => {
  const referenced = new Map<string, string[]>()
  for (const [file, source] of Object.entries(sources)) {
    for (const match of source.matchAll(REFERENCE_RE)) {
      const key = match[1]!
      referenced.set(key, [...(referenced.get(key) ?? []), file])
    }
  }

  it('finds the call sites at all, so a broken scan cannot pass vacuously', () => {
    expect(referenced.size).toBeGreaterThan(0)
  })

  it('has every referenced key in both bundles', () => {
    const missing = [...referenced.entries()]
      .filter(([key]) => !(key in enPanel) || !(key in zhPanel))
      .map(([key, files]) => `${key} (${files.join(', ')})`)
    expect(missing).toEqual([])
  })

  it('no source file still references a retired key', () => {
    const survivors = RETIRED.filter((key) => referenced.has(key)).map(
      (key) => `${key} (${referenced.get(key)?.join(', ')})`,
    )
    expect(survivors).toEqual([])
  })
})
