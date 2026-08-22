<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  modelValue?: string
  placeholder?: string
  rows?: number
  disabled?: boolean
  error?: boolean
  resize?: 'none' | 'vertical' | 'both'
  id?: string | undefined
  maxlength?: number | undefined
  mono?: boolean
}>(), {
  modelValue: '',
  placeholder: '',
  rows: 3,
  disabled: false,
  error: false,
  resize: 'vertical',
  mono: false,
})

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

// `id`/`maxlength` are genuinely optional with no default. Vue already omits
// an attribute whose bound value is `undefined` at runtime, but vue-tsc's
// template type-check rejects an explicitly-`undefined`-typed value under
// `exactOptionalPropertyTypes` even though the runtime behavior is exactly
// "attribute absent" — these casts only narrow the *static* type, the
// *value* is unchanged. Kept as literal `:id`/`:maxlength` bindings (not
// folded into a `v-bind` spread) so the id stays visible to
// vuejs-accessibility/form-control-has-label, which only recognizes a named
// attribute.
const idAttr = computed(() => props.id as string)
const maxlengthAttr = computed(() => props.maxlength as number)

function onInput(event: Event) {
  const target = event.target as HTMLTextAreaElement
  emit('update:modelValue', target.value)
}
</script>

<template>
  <textarea
    :id="idAttr"
    class="s-textarea"
    :class="[
      {
        's-textarea--error': error,
        's-textarea--disabled': disabled,
        's-textarea--mono': mono,
      },
    ]"
    :style="{ resize }"
    :value="props.modelValue"
    :placeholder="props.placeholder"
    :rows="props.rows"
    :disabled="props.disabled"
    :maxlength="maxlengthAttr"
    @input="onInput"
  />
</template>

<style scoped>
.s-textarea {
  display: block;
  width: 100%;
  padding: var(--space-2);
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-md);
  background: var(--color-bg);
  color: var(--color-fg);
  font-family: inherit;
  font-size: var(--font-size-sm);
  line-height: var(--line-normal);
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

.s-textarea--mono {
  font-family: var(--font-mono);
}

.s-textarea--disabled {
  opacity: 0.5;
  cursor: not-allowed;
  background: var(--color-surface);
}
</style>
