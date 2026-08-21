<script setup lang="ts">
import { computed, useAttrs, type StyleValue } from 'vue'
import { ChevronDownIcon } from '@heroicons/vue/20/solid'

defineOptions({ inheritAttrs: false })

const props = withDefaults(defineProps<{
  modelValue?: string | number | null
  options: Array<{ value: string | number; label: string; disabled?: boolean }>
  placeholder?: string
  disabled?: boolean
  error?: boolean
  size?: 'sm' | 'md'
  id?: string | undefined
}>(), {
  modelValue: null,
  placeholder: '',
  disabled: false,
  error: false,
  size: 'md',
})

const emit = defineEmits<{
  'update:modelValue': [value: string | number]
}>()

const attrs = useAttrs()

// The root node is the positioning wrapper (it owns the chevron overlay), so
// unforwarded attrs would land on the div and leave the select unnamed.
// `class`/`style` stay on the wrapper — callers size the control with them and
// a parent's scoped-style id only reaches the component root — while aria-*,
// name, data-* and listeners go to the native select.
// The `StyleValue` cast only narrows the static type; an absent `style` is
// still `undefined` at runtime, which Vue renders as "attribute omitted".
const wrapperAttrs = computed(() => ({
  class: attrs.class,
  style: attrs.style as StyleValue,
}))
const nativeAttrs = computed(() => {
  const { class: _class, style: _style, ...rest } = attrs
  return rest
})

const showPlaceholder = computed(() => {
  return props.modelValue === null || props.modelValue === ''
})

// `id` is genuinely optional with no default. Vue already omits an attribute
// whose bound value is `undefined` at runtime, but vue-tsc's template
// type-check rejects an explicitly-`undefined`-typed value under
// `exactOptionalPropertyTypes` even though the runtime behavior is exactly
// "attribute absent" — this cast only narrows the *static* type, the *value*
// is unchanged. Kept as a literal `:id` binding (not folded into a `v-bind`
// spread) so it stays visible to vuejs-accessibility/form-control-has-label,
// which only recognizes a named attribute.
const idAttr = computed(() => props.id as string)

function onChange(event: Event) {
  const target = event.target as HTMLSelectElement
  const option = props.options.find((o) => String(o.value) === target.value)
  if (option) {
    emit('update:modelValue', option.value)
  }
}
</script>

<template>
  <div
    class="s-select"
    :class="[
      `s-select--${size}`,
      {
        's-select--error': error,
        's-select--disabled': disabled,
        's-select--placeholder': showPlaceholder,
      },
    ]"
    v-bind="wrapperAttrs"
  >
    <select
      v-bind="nativeAttrs"
      :id="idAttr"
      class="s-select__native"
      :value="props.modelValue ?? ''"
      :disabled="props.disabled"
      @change="onChange"
    >
      <option
        v-if="placeholder"
        value=""
        disabled
      >
        {{ placeholder }}
      </option>
      <option
        v-for="opt in options"
        :key="opt.value"
        :value="opt.value"
        :disabled="opt.disabled ?? false"
      >
        {{ opt.label }}
      </option>
    </select>
    <ChevronDownIcon
      class="s-select__chevron"
      aria-hidden="true"
    />
  </div>
</template>

<style scoped>
.s-select {
  position: relative;
  display: flex;
  align-items: center;
}

.s-select__native {
  width: 100%;
  appearance: none;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg);
  color: var(--color-fg);
  font-size: var(--font-size-sm);
  padding: 0 var(--space-8) 0 var(--space-2);
  cursor: pointer;
  transition: border-color var(--transition-fast);
}

.s-select__native:focus {
  border-color: var(--color-accent);
  outline: none;
  box-shadow: var(--focus-ring);
}

.s-select--sm .s-select__native {
  min-height: 32px;
  font-size: var(--font-size-xs);
}

.s-select--md .s-select__native {
  min-height: 40px;
}

.s-select--error .s-select__native {
  border-color: var(--color-danger);
}

.s-select--error .s-select__native:focus {
  box-shadow: 0 0 0 2px var(--color-bg), 0 0 0 4px var(--color-danger);
}

.s-select--disabled .s-select__native {
  opacity: 0.5;
  cursor: not-allowed;
  background: var(--color-surface);
}

.s-select--placeholder .s-select__native {
  color: var(--color-muted);
}

.s-select__chevron {
  position: absolute;
  right: 8px;
  width: 18px;
  height: 18px;
  color: var(--color-muted);
  pointer-events: none;
}
</style>
