import { describe, it, expect, vi, afterEach } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { EditorView } from '@codemirror/view'
import { i18n } from '@shared/i18n'
import SCodeEditor from '../SCodeEditor.vue'

const wrappers: VueWrapper[] = []
function mountEditor(props: Record<string, unknown>) {
  const w = mount(SCodeEditor, { props, global: { plugins: [i18n] } })
  wrappers.push(w)
  return w
}

// Each CodeMirror-backed instance keeps a rAF-driven measure loop alive until
// `view.destroy()` runs; leaving one mounted past its test lets the next
// scheduled tick fire against a torn-down test and surface as an unrelated
// unhandled-rejection failure.
afterEach(() => {
  wrappers.splice(0).forEach((w) => w.unmount())
})

async function waitForCodeMirror(wrapper: ReturnType<typeof mountEditor>) {
  await vi.waitFor(() => {
    if (!wrapper.find('.code-editor-cm-host').exists()) throw new Error('CodeMirror not mounted yet')
  })
}

describe('SCodeEditor', () => {
  it('renders a plain textarea for non-json languages (unchanged behavior)', async () => {
    const w = mountEditor({ modelValue: 'hello', language: 'markdown' })
    await new Promise((r) => setTimeout(r, 0))
    expect(w.find('textarea.code-editor').exists()).toBe(true)
    expect(w.find('.code-editor-cm-host').exists()).toBe(false)
  })

  it('keeps the readonly textarea styling for the readonly text field', () => {
    const w = mountEditor({ modelValue: 'boom', language: 'text', readonly: true })
    const textarea = w.find('textarea.code-editor')
    expect(textarea.attributes('readonly')).toBeDefined()
  })

  it('starts as a textarea and swaps to CodeMirror for language="json"', async () => {
    const w = mountEditor({ modelValue: '{}', language: 'json' })
    expect(w.find('textarea.code-editor').exists()).toBe(true)
    await waitForCodeMirror(w)
    expect(w.find('textarea.code-editor').exists()).toBe(false)
    expect(w.find('.cm-content').exists()).toBe(true)
  })

  it('round-trips v-model: an editor-originated change emits update:modelValue with the new doc', async () => {
    // jsdom has no contenteditable editing implementation — a synthetic
    // 'beforeinput'/'input' event on .cm-content does not run CodeMirror's
    // real DOM-mutation pipeline the way an actual keystroke would in a
    // browser, so it cannot stand in for "the user typed" here (OQ-1). CM6's
    // own `EditorView.findFromDOM` is a public API for recovering the view
    // instance from its DOM node; dispatching through it exercises the exact
    // same `updateListener` -> `emit` path a real keystroke would drive, so
    // it verifies the wiring this AC is actually about. The full DOM-input
    // pipeline itself needs a real browser — covered by the `verify` skill
    // pass in Step 5.4, not a unit test.
    const w = mountEditor({ modelValue: '{}', language: 'json' })
    await waitForCodeMirror(w)
    const host = w.find('.code-editor-cm-host').element as HTMLElement
    const view = EditorView.findFromDOM(host)
    expect(view).not.toBeNull()
    view!.dispatch({ changes: { from: 1, to: 1, insert: '"a":1' } })
    expect(w.emitted('update:modelValue')?.at(-1)).toEqual(['{"a":1}'])
  })

  it('applies a programmatic write to the CodeMirror doc without an echo loop', async () => {
    const w = mountEditor({ modelValue: '{}', language: 'json' })
    await waitForCodeMirror(w)
    await w.setProps({ modelValue: '{"loaded":true}' })
    await vi.waitFor(() => {
      if (w.find('.cm-content').text() !== '{"loaded":true}') throw new Error('doc not updated yet')
    })
    expect(w.find('.cm-content').text()).toBe('{"loaded":true}')
    // The view's updateListener fires once for the dispatched change and
    // relays it straight back out (idempotent — the parent already holds
    // this value). What AC-4 rules out is a *loop*: the relayed value must
    // not re-enter the watch and trigger a second dispatch, which is what
    // would actually reset the caret. One echo, not a chain of them.
    const emitted = w.emitted('update:modelValue')
    expect(emitted).toHaveLength(1)
    expect(emitted?.[0]).toEqual(['{"loaded":true}'])
  })

  it('surfaces a positioned lint diagnostic for malformed JSON', async () => {
    const w = mountEditor({ modelValue: '{"a":}', language: 'json' })
    await waitForCodeMirror(w)
    await vi.waitFor(() => {
      if (!w.find('.cm-lint-marker').exists()) throw new Error('lint marker not rendered yet')
    })
    expect(w.find('.cm-lint-marker').exists()).toBe(true)
  })
})
