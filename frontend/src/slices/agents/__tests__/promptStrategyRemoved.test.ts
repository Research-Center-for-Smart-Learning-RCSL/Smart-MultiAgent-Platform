import { describe, expect, it } from 'vitest'

import en from '../locales/en.json'
import zhTW from '../locales/zh-TW.json'

/**
 * AC-10 — the §9.2 lazy prompt mechanism stays removed, i18n included.
 *
 * The repo-wide gate under `scripts/` greps for the removed identifiers, but two of
 * these keys are *nested* (`agents.form.strategies.full` / `.lazy`) and their leaf
 * names are the bare words "full" and "lazy" — they match no identifier pattern, so
 * the grep cannot see them. Nothing else fails either: vue-i18n resolves a missing
 * key to the key string and only warns, so a stale `strategies` block would sit in
 * both locale files indefinitely. Hence this explicit assertion.
 */

type Form = Record<string, unknown>

const locales: ReadonlyArray<readonly [string, Form]> = [
  ['en', (en as { agents: { form: Form } }).agents.form],
  ['zh-TW', (zhTW as { agents: { form: Form } }).agents.form],
]

describe('§9.2 prompt strategy i18n removal', () => {
  it.each(locales)('%s: agents.form has no nested strategies block', (_name, form) => {
    expect(form.strategies).toBeUndefined()
  })

  it.each(locales)('%s: agents.form has no promptStrategy* keys', (_name, form) => {
    const leaked = Object.keys(form).filter((k) => k.toLowerCase().startsWith('promptstrategy'))
    expect(leaked).toEqual([])
  })

  it.each(locales)('%s: the surviving systemPrompt keys are untouched', (_name, form) => {
    // Guards against the removal over-reaching into the neighbouring keys.
    expect(form.systemPrompt).toBeTruthy()
    expect(form.systemPromptPlaceholder).toBeTruthy()
  })
})
