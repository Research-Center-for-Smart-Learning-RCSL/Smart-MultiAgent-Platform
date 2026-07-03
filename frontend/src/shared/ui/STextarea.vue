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
}>(), {
  modelValue: '',
  placeholder: '',
  rows: 3,
  disabled: false,
  error: false,
  resize: 'vertical',
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
