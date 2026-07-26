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
})
