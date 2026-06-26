<script setup lang="ts">
withDefaults(defineProps<{
  modelValue?: string
  placeholder?: string
  rows?: number
  disabled?: boolean
  error?: boolean
  resize?: 'none' | 'vertical' | 'both'
  id?: string | undefined
}>(), {
  modelValue: '',
  placeholder: '',
  rows: 3,
  disabled: false,
  error: false,
  resize: 'vertical',
  id: undefined,
})

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

function onInput(event: Event) {
  const target = event.target as HTMLTextAreaElement
  emit('update:modelValue', target.value)
}
</script>

<template>
  <textarea
    :id="id"
    class="s-textarea"
    :class="[
      {
        's-textarea--error': error,
        's-textarea--disabled': disabled,
      },
    ]"
    :style="{ resize }"
    :value="modelValue"
    :placeholder="placeholder"
    :rows="rows"
    :disabled="disabled"
    @input="onInput"
  />
</template>

<style scoped>
.s-textarea {
  display: block;
  width: 100%;
  padding: 8px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg);
  color: var(--color-fg);
  font-family: inherit;
  font-size: 0.875rem;
  line-height: 1.5;
  transition: border-color var(--transition-fast);
}

.s-textarea::placeholder {
  color: var(--color-muted);
}

.s-textarea:focus {
  border-color: var(--color-accent);
  outline: none;
  box-shadow: var(--focus-ring);
}

.s-textarea--error {
  border-color: var(--color-danger);
}

.s-textarea--error:focus {
  box-shadow: 0 0 0 2px var(--color-bg), 0 0 0 4px var(--color-danger);
}

.s-textarea--disabled {
  opacity: 0.5;
  cursor: not-allowed;
  background: var(--color-surface);
}
</style>
