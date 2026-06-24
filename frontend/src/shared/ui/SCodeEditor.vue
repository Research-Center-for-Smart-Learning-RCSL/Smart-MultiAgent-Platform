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
  placeholder: undefined,
  language: 'text',
  rows: 8,
  readonly: false,
  id: undefined,
})

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

const minHeight = computed(() => `${props.rows * 1.5 * 13}px`)

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
    :id="id"
    class="code-editor"
    :class="`code-editor--${language}`"
    :value="modelValue"
    :placeholder="placeholder"
    :readonly="readonly"
    :rows="rows"
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
  outline: var(--focus-ring);
}

.code-editor[readonly] {
  opacity: 0.7;
  cursor: default;
}
</style>
