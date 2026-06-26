<script setup lang="ts">
import { computed } from 'vue'
import { ChevronDownIcon } from '@heroicons/vue/20/solid'

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
  id: undefined,
})

const emit = defineEmits<{
  'update:modelValue': [value: string | number]
}>()

const showPlaceholder = computed(() => {
  return props.modelValue === null || props.modelValue === ''
})

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
  >
    <select
      :id="id"
      class="s-select__native"
      :value="modelValue ?? ''"
      :disabled="disabled"
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
        :disabled="opt.disabled"
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
  font-size: 0.875rem;
  padding: 0 32px 0 8px;
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
  font-size: 0.75rem;
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
