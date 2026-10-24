import { describe, expect, it } from 'vitest'

import { defaultWakeupConfig, normalizeWakeupConfig } from '../workflow'

describe('wakeup config defaults', () => {
  it('mirrors the backend defaults for shared fields', () => {
    // Mirrors contexts/orchestration/domain/models.py.
    const config = defaultWakeupConfig()

    expect(config.triggers.every_n_messages.n).toBe(3)
    expect(config.triggers.silence_minutes.autostop_rounds).toBe(5)
    expect(config.triggers.silence_minutes.observer_autostop_rounds).toBe(50)
  })

  it('preserves the observer autostop default for partial nested configs', () => {
    const config = normalizeWakeupConfig({
      triggers: { silence_minutes: { enabled: true } },
    })

    expect(config.triggers.silence_minutes.observer_autostop_rounds).toBe(50)
  })

  it('has no soft_bounds of its own', () => {
    // soft_bounds has no default: inventing one here would merge an empty
    // object over the designer's stored bounds on the next save.
    expect('soft_bounds' in defaultWakeupConfig()).toBe(false)
  })

  it('passes through root keys it does not model', () => {
    // R15.08 soft_bounds has no editor control, so the editor must round-trip
    // it untouched rather than dropping it from the payload it sends back.
    const config = normalizeWakeupConfig({
      triggers: { every_n_messages: { enabled: true, n: 8 } },
      soft_bounds: { n_min: 5, n_max: 10 },
      designer_note: 'x',
    })

    expect(config.soft_bounds).toEqual({ n_min: 5, n_max: 10 })
    expect(config.designer_note).toBe('x')
    expect(config.triggers.every_n_messages.n).toBe(8)
  })

  it('clones passed-through keys instead of aliasing the input', () => {
    // `raw` is the query-cache entry; a live reference into it would let a
    // caller mutate cached server state in place.
    const raw = { triggers: {}, soft_bounds: { n_min: 5 } }
    const config = normalizeWakeupConfig(raw)

    expect(config.soft_bounds).not.toBe(raw.soft_bounds)
    expect(config.soft_bounds).toEqual({ n_min: 5 })
  })

  it('passes through root keys on the legacy flat shape too', () => {
    const config = normalizeWakeupConfig({
      every_n_messages: 4,
      soft_bounds: { n_min: 2 },
    })

    expect(config.soft_bounds).toEqual({ n_min: 2 })
    expect(config.triggers.every_n_messages.n).toBe(4)
  })
})

describe('wakeup arrival trigger (R15.26)', () => {
  it('defaults to off and keeps a stored value', () => {
    expect(defaultWakeupConfig().triggers.on_join).toEqual({ enabled: false })
    expect(normalizeWakeupConfig({ triggers: { on_join: { enabled: true } } }).triggers.on_join.enabled)
      .toBe(true)
  })

  it('reads a non-boolean as off, as the server does', () => {
    const config = normalizeWakeupConfig({ triggers: { on_join: { enabled: 'true' } } })

    expect(config.triggers.on_join.enabled).toBe(false)
  })

  it('is off under call_only and on the legacy flat shape', () => {
    const callOnly = normalizeWakeupConfig({
      triggers: { call_only: { enabled: true }, on_join: { enabled: true } },
    })
    const legacy = normalizeWakeupConfig({ every_n_messages: 4 })

    expect(callOnly.triggers.on_join.enabled).toBe(false)
    expect(legacy.triggers.on_join.enabled).toBe(false)
  })
})

describe('wakeup counting mode and self-toggle grant (R15.01, R15.06)', () => {
  it('defaults to counting every message and granting nothing', () => {
    const config = defaultWakeupConfig()

    expect(config.triggers.every_n_messages.count).toBe('all')
    expect(config.self_toggle).toEqual([])
  })

  it('keeps a stored human count and resolves an unknown one to all', () => {
    const human = normalizeWakeupConfig({ triggers: { every_n_messages: { count: 'human' } } })
    const unknown = normalizeWakeupConfig({ triggers: { every_n_messages: { count: 'robots' } } })

    expect(human.triggers.every_n_messages.count).toBe('human')
    expect(unknown.triggers.every_n_messages.count).toBe('all')
  })

  it('keeps only toggleable trigger names in the grant', () => {
    const config = normalizeWakeupConfig({
      triggers: {},
      self_toggle: ['silence_minutes', 'call_only', 7],
    })

    expect(config.self_toggle).toEqual(['silence_minutes'])
  })
})
