import { describe, it, expect } from 'vitest'

// The seed help text shipped for months saying the exact inverse of the
// provider contracts -- "applies only to OpenAI; ignored by Gemini" -- while
// no adapter forwarded it anywhere. Nothing could catch that: a locale string
// renders whether or not it is true, and the disabled-reason key did not exist
// at all, so a missing translation would have surfaced as a raw key in zh-TW
// only, at runtime, on a screen nobody screenshots.
import en from '../locales/en.json'
import zhTW from '../locales/zh-TW.json'

type Bundle = { agents: { form: Record<string, string> } }

const bundles: [string, Bundle][] = [
  ['en', en as Bundle],
  ['zh-TW', zhTW as Bundle],
]

const SAMPLING_KEYS = [
  'samplingHelp',
  'samplingDisabledReason',
  'seed',
  'seedHelp',
  'seedDisabledReason',
] as const

describe('agents locales: sampling and seed copy', () => {
  for (const [name, bundle] of bundles) {
    it(`${name} carries every sampling/seed key the form renders`, () => {
      for (const key of SAMPLING_KEYS) {
        expect(typeof bundle.agents.form[key], key).toBe('string')
        expect(bundle.agents.form[key], key).not.toBe('')
      }
    })

    it(`${name} interpolates the model name into the disabled reasons`, () => {
      expect(bundle.agents.form.seedDisabledReason).toContain('{model}')
      expect(bundle.agents.form.samplingDisabledReason).toContain('{model}')
    })

    it(`${name} states seed support per model, never per provider`, () => {
      // Provider support is a per-row fact resolved from the capability table.
      // Naming a provider in the static help is what made the old string wrong
      // the moment any row disagreed with it.
      const help = bundle.agents.form.seedHelp
      for (const provider of ['OpenAI', 'Anthropic', 'Gemini', 'Claude']) {
        expect(help, provider).not.toContain(provider)
      }
    })

    it(`${name} disclaims rather than promises reproducible output`, () => {
      // Q-8: a seed is a decoding input, not a reproducibility guarantee --
      // provider-side changes and nondeterministic execution remain possible.
      // Asserting the disclaimer is present, not merely that some forbidden
      // word is absent: a rewrite that drops the caveat entirely is the
      // regression, and an absence check would pass on it.
      const help = bundle.agents.form.seedHelp
      expect(help).toContain(name === 'en' ? 'does not guarantee' : '不保證')
    })
  }
})
