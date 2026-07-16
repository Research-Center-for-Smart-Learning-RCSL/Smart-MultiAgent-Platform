import { describe, expect, it } from 'vitest'

import en from '../locales/en.json'
import zhTW from '../locales/zh-TW.json'

/**
 * AC-10 — the §9.2 lazy prompt mechanism stays removed, i18n included.
 *
 * This file covers exactly the part the repo-wide grep gate cannot: two of the removed
 * keys were *nested* (`agents.form.strategies.full` / `.lazy`) and their leaf names are
 * the bare words "full" and "lazy", so they match no identifier pattern. Nothing else
 * fails either — vue-i18n resolves a missing key to the key string and only warns, so a
 * stale `strategies` block would sit in both locale files indefinitely.
 *
 * Every *flat* removed key is already the grep's job, and asserting it here as well
 * would be worse than redundant: this file lives under `frontend/src/`, which the gate
 * scans, so spelling the removed identifier to assert its absence is what makes the
 * gate fail on the very file that checks it (D-11).
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

  it.each(locales)('%s: the surviving systemPrompt keys are untouched', (_name, form) => {
    // Guards against the removal over-reaching into the neighbouring keys.
    expect(form.systemPrompt).toBeTruthy()
    expect(form.systemPromptPlaceholder).toBeTruthy()
  })
})
