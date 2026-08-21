<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { EditorView as EditorViewInstance } from '@codemirror/view'

const props = withDefaults(defineProps<{
  modelValue: string
  placeholder?: string
  language?: 'json' | 'yaml' | 'markdown' | 'text'
  rows?: number
  readonly?: boolean
  id?: string
}>(), {
  language: 'text',
  rows: 8,
  readonly: false,
})

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

const { t } = useI18n()

const minHeight = computed(() => `${props.rows * 1.5 * 13}px`)

// `id`/`placeholder` are genuinely optional with no default. Vue already
// omits an attribute whose bound value is `undefined` at runtime, but
// vue-tsc's template type-check rejects an explicitly-`undefined`-typed
// value under `exactOptionalPropertyTypes` even though the runtime behavior
// is exactly "attribute absent" — these casts only narrow the *static* type,
// the *value* is unchanged. Kept as literal `:id`/`:placeholder` bindings
// (not folded into a `v-bind` spread) so the id stays visible to
// vuejs-accessibility/form-control-has-label, which only recognizes a named
// attribute.
const idAttr = computed(() => props.id as string)
const placeholderAttr = computed(() => props.placeholder as string)

function onInput(e: Event) {
  const target = e.target as HTMLTextAreaElement
  emit('update:modelValue', target.value)
}

// Only reached on the textarea fallback path: permanently for the
// non-`json` languages, and transiently for `json` while the CodeMirror
// chunk is still loading. Once CodeMirror mounts, `indentWithTab` (added
// explicitly below — `defaultKeymap` deliberately omits it, since binding
// Tab is an accessibility trade-off CodeMirror leaves to the embedder)
// takes over.
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Tab') {
    e.preventDefault()
    const target = e.target as HTMLTextAreaElement
    const start = target.selectionStart
    const end = target.selectionEnd
    const value = target.value
    const updated = value.substring(0, start) + '  ' + value.substring(end)
    emit('update:modelValue', updated)
    requestAnimationFrame(() => {
      target.selectionStart = start + 2
      target.selectionEnd = start + 2
    })
  }
}

// --- CodeMirror 6, JSON only (2026-07-16-code-editor-syntax-highlighting) ---
// The other language values keep the plain textarea above: no grammar ships
// for them yet (Q-4), so mounting CodeMirror for them would cost bytes for
// no highlighting.
const textareaRef = ref<HTMLTextAreaElement | null>(null)
const cmHostRef = ref<HTMLDivElement | null>(null)
const cmActive = ref(false)

let view: EditorViewInstance | null = null
let disposed = false

onMounted(async () => {
  if (props.language !== 'json') return

  // A wrapping SFormField assigns the textarea's `id`/`aria-describedby`/
  // `aria-invalid` from its own onMounted(syncAria) — which, per Vue's
  // mount order, runs right after this child's onMounted returns. Waiting a
  // tick lets that assignment land before we read it below, so the
  // CodeMirror content element inherits the same label association the
  // textarea would have had (AC-8): a `contenteditable` does not get one for
  // free the way `SFormField`'s `querySelector('input, select, textarea')`
  // gives a `<textarea>`.
  await nextTick()
  if (disposed) return

  const textarea = textareaRef.value
  const inheritedId = textarea?.id || idAttr.value
  const describedBy = textarea?.getAttribute('aria-describedby')
  const invalid = textarea?.getAttribute('aria-invalid')

  const cm = await import('./codeMirrorJson')
  if (disposed) return

  cmActive.value = true
  await nextTick()
  const host = cmHostRef.value
  if (disposed || !host) return

  const contentAttrs: Record<string, string> = {}
  if (inheritedId) contentAttrs.id = inheritedId
  if (describedBy) contentAttrs['aria-describedby'] = describedBy
  if (invalid) contentAttrs['aria-invalid'] = invalid

  // jsonParseLinter's Diagnostic.message is JSON.parse's own error text,
  // which V8 always renders in English — it is not one of CodeMirror's own
  // phrase()-driven strings, so EditorState.phrases cannot translate it.
  // (Verified: none of the extensions below call view.state.phrase() —
  // that only happens inside the lint *panel*, which is not included here.)
  // Replacing the message keeps AC-9 satisfied without losing the parse
  // position/severity, which is the actual value this task adds.
  const baseJsonLint = cm.jsonParseLinter()
  const localizedJsonLint = (v: EditorViewInstance) =>
    baseJsonLint(v).map((d) => ({ ...d, message: t('shared.codeEditor.invalidJson') }))

  const themeCompartment = new cm.Compartment()

  view = new cm.EditorView({
    parent: host,
    doc: props.modelValue,
    extensions: [
      cm.lineNumbers(),
      cm.history(),
      // indentWithTab first: defaultKeymap deliberately doesn't bind Tab
      // (CodeMirror leaves that trade-off to the embedder), so without it
      // Tab just moves focus to the next form control instead of indenting.
      cm.keymap.of([cm.indentWithTab, ...cm.defaultKeymap, ...cm.historyKeymap]),
      cm.syntaxHighlighting(cm.defaultHighlightStyle),
      cm.json(),
      cm.linter(localizedJsonLint),
      cm.lintGutter(),
      // No call site combines language="json" with :placeholder today, but
      // the textarea fallback supports it — CodeMirror needs its own
      // extension for the same affordance, or it silently disappears the
      // moment CodeMirror mounts.
      ...(placeholderAttr.value ? [cm.placeholder(placeholderAttr.value)] : []),
      cm.EditorView.contentAttributes.of(contentAttrs),
      // Fixed at construction: no call site toggles `readonly` on a live
      // CodeMirror-backed instance today.
      cm.EditorState.readOnly.of(props.readonly),
      cm.EditorView.editable.of(!props.readonly),
      // Compartment seam for a future bespoke HighlightStyle (§16 FU-3);
      // the theme itself just reads the app's CSS custom properties, so
      // light/dark switching via `data-theme` needs no reconfiguration.
      themeCompartment.of(
        cm.EditorView.theme({
          '&': {
            color: 'var(--color-fg)',
            backgroundColor: 'var(--color-surface)',
            border: '1px solid var(--color-border)',
            borderRadius: 'var(--radius-md)',
            fontFamily: 'var(--font-mono)',
            fontSize: '13px',
            opacity: props.readonly ? '0.7' : '1',
          },
          '.cm-content': {
            padding: '12px',
            minHeight: minHeight.value,
            caretColor: 'var(--color-fg)',
          },
          '.cm-scroller': {
            fontFamily: 'var(--font-mono)',
            lineHeight: '1.5',
          },
          '.cm-gutters': {
            backgroundColor: 'var(--color-surface)',
            color: 'var(--color-muted)',
            border: 'none',
            borderRight: '1px solid var(--color-border)',
          },
          '&.cm-focused': {
            outline: 'none',
            borderColor: 'var(--color-accent)',
            boxShadow: 'var(--focus-ring)',
          },
        }),
      ),
      cm.EditorView.updateListener.of((update) => {
        if (!update.docChanged) return
        emit('update:modelValue', update.state.doc.toString())
      }),
    ],
  })
})

// Standard CodeMirror/Vue v-model bridge: compare against the view's own doc
// before dispatching, so the editor's own change (relayed back through
// `update:modelValue`) never bounces back in as a redundant dispatch that
// would reset the caret.
watch(() => props.modelValue, (value) => {
  if (!view) return
  const current = view.state.doc.toString()
  if (current === value) return
  view.dispatch({ changes: { from: 0, to: current.length, insert: value } })
})

onBeforeUnmount(() => {
  disposed = true
  view?.destroy()
  view = null
})
</script>

<template>
  <textarea
    v-if="!cmActive"
    :id="idAttr"
    ref="textareaRef"
    class="code-editor"
    :value="modelValue"
    :placeholder="placeholderAttr"
    :readonly="props.readonly"
    :rows="props.rows"
    :style="{ minHeight }"
    spellcheck="false"
    autocomplete="off"
    autocorrect="off"
    autocapitalize="off"
    @input="onInput"
    @keydown="onKeydown"
  />
  <div
    v-else
    ref="cmHostRef"
    class="code-editor-cm-host"
  />
</template>

<style scoped>
.code-editor {
  display: block;
  width: 100%;
  padding: 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  color: var(--color-fg);
  font-family: var(--font-mono);
  font-size: var(--font-size-code);
  line-height: var(--line-normal);
  white-space: pre-wrap;
  word-wrap: break-word;
  resize: vertical;
  outline: none;
  transition: border-color var(--transition-fast);
}

.code-editor::placeholder {
  color: var(--color-muted);
}

.code-editor:focus {
  border-color: var(--color-accent);
  outline: none;
  box-shadow: var(--focus-ring);
}

.code-editor[readonly] {
  opacity: 0.7;
  cursor: default;
}

.code-editor-cm-host {
  display: block;
  width: 100%;
}
</style>
