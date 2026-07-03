<script setup lang="ts">
import { computed } from 'vue'

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
</script>

<template>
  <textarea
    :id="idAttr"
    class="code-editor"
    :class="`code-editor--${language}`"
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
  font-size: 13px;
  line-height: 1.5;
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
</style>
