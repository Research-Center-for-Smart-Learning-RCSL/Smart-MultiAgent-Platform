<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  modelValue?: string
  value: string
  disabled?: boolean
  name?: string
  id?: string | undefined
}>(), {
  modelValue: '',
  disabled: false,
  name: '',
})

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

// id is genuinely optional (no default); omit the attr entirely rather than
// passing an explicit `undefined` value, which exactOptionalPropertyTypes
// forbids.
const idAttrs = computed(() => (props.id !== undefined ? { id: props.id } : {}))

function onChange() {
  emit('update:modelValue', props.value)
}
</script>

<template>
  <label
    class="s-radio"
    :class="{ 's-radio--disabled': disabled }"
  >
    <input
      v-bind="idAttrs"
      type="radio"
      class="s-radio__native"
      :name="props.name"
      :value="props.value"
      :checked="props.modelValue === props.value"
      :disabled="props.disabled"
      @change="onChange"
    >
    <span
      class="s-radio__circle"
      :class="{ 's-radio__circle--selected': modelValue === value }"
      aria-hidden="true"
    >
      <span
        v-if="modelValue === value"
        class="s-radio__dot"
      />
    </span>
    <span
      v-if="$slots.default"
      class="s-radio__label"
    >
      <slot />
    </span>
  </label>
</template>

<style scoped>
.s-radio {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  cursor: pointer;
  min-height: 44px;
}

.s-radio--disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.s-radio__native {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.s-radio__circle {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  flex-shrink: 0;
  border: 1.5px solid var(--color-border-strong);
  border-radius: var(--radius-full);
  background: var(--color-bg);
  transition:
    border-color var(--transition-fast);
}

.s-radio__circle--selected {
  border-color: var(--color-accent);
}

/* The native input is clipped to a 1px rect, so an outline on it would be
   clipped with it. The visible circle is a sibling and has to carry the ring. */
.s-radio__native:focus-visible ~ .s-radio__circle {
  outline: 2px solid var(--color-accent);
  outline-offset: 2px;
}

.s-radio__dot {
  width: 8px;
  height: 8px;
  border-radius: var(--radius-full);
  background: var(--color-accent);
}

.s-radio__label {
  font-size: var(--font-size-sm);
  color: var(--color-fg);
  user-select: none;
}
</style>
