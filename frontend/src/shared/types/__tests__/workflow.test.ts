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

  it('passes through root keys on the legacy flat shape too', () => {
    const config = normalizeWakeupConfig({
      every_n_messages: 4,
      soft_bounds: { n_min: 2 },
    })

    expect(config.soft_bounds).toEqual({ n_min: 2 })
    expect(config.triggers.every_n_messages.n).toBe(4)
  })
})
