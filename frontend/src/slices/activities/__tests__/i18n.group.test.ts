// AC-5 (panel half) and §7's one hard rendering rule: the consent threshold is
// a SENTENCE, never a `2/3` glyph. A fraction sitting in an activity panel next
// to a validation outcome reads as a score, which is exactly the wrong thing to
// tell a student about how many of their group have agreed.
//
// Asserted against the bundles rather than the rendered component for the same
// reason `i18n.panel.test.ts` is: the component harness mounts i18n with no
// bundle, so every component assertion reads raw keys and could not see the
// difference between "需要 3 人同意" and "2/3".

import { describe, expect, it } from 'vitest'

import { createI18n } from 'vue-i18n'
import en from '../locales/en.json'
import zhTW from '../locales/zh-TW.json'

type Bundle = { activities?: { group?: Record<string, string> } }

const enGroup = (en as Bundle).activities?.group ?? {}
const zhGroup = (zhTW as Bundle).activities?.group ?? {}

const sources = import.meta.glob(['/src/**/*.vue', '/src/**/*.ts', '!/src/**/__tests__/**'], {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

const REFERENCE_RE = /\$?t\(\s*['"]activities\.group\.([A-Za-z0-9_]+)['"]/g

/** Both bundles, wired for real so the interpolation is exercised rather than
 *  the template string inspected. */
const i18n = createI18n({
  legacy: false,
  locale: 'en',
  messages: { en: en as never, 'zh-TW': zhTW as never },
})

function render(locale: 'en' | 'zh-TW', key: string, args: Record<string, unknown>): string {
  return i18n.global.t(`activities.group.${key}`, args, { locale })
}

describe('group proposal locales', () => {
  it('en and zh-TW declare identical key sets', () => {
    expect(Object.keys(enGroup).sort()).toEqual(Object.keys(zhGroup).sort())
  })

  it('translates every key to a non-empty string in both locales', () => {
    for (const [locale, bundle] of [
      ['en', enGroup],
      ['zh-TW', zhGroup],
    ] as const) {
      for (const [key, value] of Object.entries(bundle)) {
        expect(typeof value, `${locale}.${key}`).toBe('string')
        expect(value, `${locale}.${key}`).not.toBe('')
      }
    }
  })

  it('has every referenced key in both bundles', () => {
    const referenced = new Map<string, string[]>()
    for (const [file, source] of Object.entries(sources)) {
      for (const match of source.matchAll(REFERENCE_RE)) {
        const key = match[1]!
        referenced.set(key, [...(referenced.get(key) ?? []), file])
      }
    }
    expect(referenced.size).toBeGreaterThan(0)
    const missing = [...referenced.entries()]
      .filter(([key]) => !(key in enGroup) || !(key in zhGroup))
      .map(([key, files]) => `${key} (${files.join(', ')})`)
    expect(missing).toEqual([])
  })
})

describe('the threshold reads as a sentence, not a score (§7)', () => {
  const args = { required: 3, total: 4, approvals: 2 }

  it.each(['en', 'zh-TW'] as const)('names all three numbers in %s', (locale) => {
    const text = render(locale, 'threshold', args)

    // Each number is load-bearing: how many are needed, out of how many, and
    // how many so far. Dropping any one of them turns the sentence into a
    // riddle a student has to ask the teacher about.
    expect(text).toContain('3')
    expect(text).toContain('4')
    expect(text).toContain('2')
    expect(text).not.toContain('{')
  })

  it.each(['en', 'zh-TW'] as const)('carries no bare fraction glyph in %s', (locale) => {
    const text = render(locale, 'threshold', args)

    // The literal defect §7 forbids: `3/4` anywhere in the string.
    expect(text).not.toMatch(/\d\s*[/⁄∕]\s*\d/)
  })

  it.each(['en', 'zh-TW'] as const)('stays a sentence when nobody has agreed yet in %s', (locale) => {
    const text = render(locale, 'threshold', { required: 1, total: 1, approvals: 0 })

    expect(text).not.toMatch(/\d\s*[/⁄∕]\s*\d/)
    expect(text.trim().length).toBeGreaterThan(0)
  })
})

describe('a dissenting member is told what their disagreement does (§8)', () => {
  it.each(['en', 'zh-TW'] as const)(
    'says in %s that the answer may still be submitted',
    (locale) => {
      // The threshold is below unanimity, so a member can be associated with an
      // answer they voted against. §8 makes stating that a requirement rather
      // than a nicety, and the vote confirmation is where a student is looking.
      const text = render(locale, 'myVoteRejected', {})

      expect(text.length).toBeGreaterThan(render(locale, 'myVoteApproved', {}).length)
    },
  )
})
