import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { i18n } from '@shared/i18n'
import { normalizeWakeupConfig, type WakeupConfig } from '@shared/types/workflow'
import SWakeupEditor from '../SWakeupEditor.vue'

// R15.01 counting mode and R15.06 self-toggle grant
// (docs/tasks/2026-10-12-wakeup-human-count-and-trigger-toggle, AC-9).

function mountEditor(raw: unknown) {
  return mount(SWakeupEditor, {
    props: { modelValue: normalizeWakeupConfig(raw) },
    global: { plugins: [i18n] },
  })
}

function lastEmitted(w: ReturnType<typeof mountEditor>): WakeupConfig {
  const events = w.emitted('update:modelValue') ?? []
  return events[events.length - 1]?.[0] as WakeupConfig
}

async function openAdvanced(w: ReturnType<typeof mountEditor>): Promise<void> {
  await w.find('.wakeup-advanced__toggle').trigger('click')
}

describe('SWakeupEditor counting mode', () => {
  it('saves a human-only count', async () => {
    const w = mountEditor({ triggers: { every_n_messages: { enabled: true, n: 3 } } })

    await w.find('select').setValue('human')

    expect(lastEmitted(w).triggers.every_n_messages.count).toBe('human')
  })

  it('hides the count with the trigger off', () => {
    const w = mountEditor({ triggers: { every_n_messages: { enabled: false } } })

    expect(w.find('select').exists()).toBe(false)
  })
})

describe('SWakeupEditor self-toggle grant', () => {
  it('grants and revokes one trigger at a time', async () => {
    const w = mountEditor({ triggers: { silence_minutes: { enabled: false } } })
    await openAdvanced(w)
    const switches = w.findAll('.wakeup-inline-toggle button, .wakeup-inline-toggle input')
    // The first inline toggle is "may reply while nobody is present"; the next two are the grants.
    const silence = switches[2]

    await silence.trigger('click')
    expect(lastEmitted(w).self_toggle).toEqual(['silence_minutes'])

    await silence.trigger('click')
    expect(lastEmitted(w).self_toggle).toEqual([])
  })

  it('disables the grant in call mode', async () => {
    const w = mountEditor({ triggers: { call_only: { enabled: true } } })
    await openAdvanced(w)
    const switches = w.findAll('.wakeup-inline-toggle button, .wakeup-inline-toggle input')

    expect(switches[1].attributes('disabled')).toBeDefined()
    expect(switches[2].attributes('disabled')).toBeDefined()
  })
})
