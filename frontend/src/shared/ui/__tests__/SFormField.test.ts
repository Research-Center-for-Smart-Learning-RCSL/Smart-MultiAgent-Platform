import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { h, ref } from 'vue'
import { i18n } from '@shared/i18n'
import SFormField from '../SFormField.vue'
import SCodeEditor from '../SCodeEditor.vue'

describe('SFormField', () => {
  it('assigns id/aria-describedby/aria-invalid to a native control on mount', () => {
    const w = mount(SFormField, {
      props: { label: 'Name', name: 'agent.name', error: 'Required' },
      slots: { default: () => h('input', { type: 'text' }) },
    })
    const input = w.find('input')
    expect(input.attributes('id')).toBe('agent.name')
    expect(input.attributes('aria-describedby')).toBe('agent.name-error')
    expect(input.attributes('aria-invalid')).toBe('true')
  })

  // Regression: a code-review finding (2026-07-18) caught that once
  // SCodeEditor swaps its textarea for CodeMirror's contenteditable div,
  // SFormField's querySelector('input, select, textarea') no longer matches
  // anything — so a validation error appearing *after* mount silently never
  // reached the CodeMirror-backed field's aria-invalid/aria-describedby.
  it('re-syncs aria-invalid/aria-describedby onto a CodeMirror-backed field after an error appears post-mount', async () => {
    const error = ref('')
    const w = mount(
      {
        components: { SFormField, SCodeEditor },
        setup: () => ({ error }),
        template: `
          <SFormField label="Config" name="config.parameters" :error="error">
            <SCodeEditor model-value="{}" language="json" />
          </SFormField>
        `,
      },
      { global: { plugins: [i18n] } },
    )

    // SCodeEditor loads CodeMirror through a dynamic import, so the wait is on
    // a module fetch, not a render tick. `waitFor`'s 1s default is enough for
    // this file alone and not enough when the full suite has every worker
    // transforming at once, which made this the one order-dependent failure in
    // the suite. The budget is generous because the cost of it being too small
    // is a red build that reproduces nowhere.
    const untilLoaded = { timeout: 15_000 }
    await vi.waitFor(() => {
      if (!w.find('.cm-content').exists()) throw new Error('CodeMirror not mounted yet')
    }, untilLoaded)
    const cmContent = w.find('.cm-content')
    expect(cmContent.attributes('id')).toBe('config.parameters')
    expect(cmContent.attributes('aria-invalid')).toBeUndefined()

    error.value = 'Parameters must be valid JSON.'
    await vi.waitFor(() => {
      if (w.find('.cm-content').attributes('aria-invalid') !== 'true') throw new Error('aria-invalid not synced yet')
    }, untilLoaded)
    expect(w.find('.cm-content').attributes('aria-describedby')).toBe('config.parameters-error')

    w.unmount()
    // The per-test budget has to clear the waits above, or the test times out
    // before they do and the raised `waitFor` timeout buys nothing.
  }, 25_000)
})
