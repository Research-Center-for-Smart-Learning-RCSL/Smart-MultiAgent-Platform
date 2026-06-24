<script setup lang="ts">
import { ref, watch } from 'vue'

const props = withDefaults(defineProps<{
  modelValue?: boolean
  disabled?: boolean
  indeterminate?: boolean
  id?: string | undefined
}>(), {
  modelValue: false,
  disabled: false,
  indeterminate: false,
  id: undefined,
})

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

const inputRef = ref<HTMLInputElement | null>(null)

watch(() => props.indeterminate, (val) => {
  if (inputRef.value) {
    inputRef.value.indeterminate = val
  }
}, { immediate: true })

function onChange() {
  emit('update:modelValue', !props.modelValue)
}
</script>

<template>
  <label
    class="s-checkbox"
    :class="{ 's-checkbox--disabled': disabled }"
  >
    <input
      :id="id"
      ref="inputRef"
      type="checkbox"
      class="s-checkbox__native"
      :checked="modelValue"
      :disabled="disabled"
      :indeterminate="indeterminate"
      @change="onChange"
    >
    <span
      class="s-checkbox__box"
      :class="{
        's-checkbox__box--checked': modelValue,
        's-checkbox__box--indeterminate': indeterminate,
      }"
      aria-hidden="true"
    >
      <svg
        v-if="modelValue && !indeterminate"
        class="s-checkbox__icon"
        viewBox="0 0 14 14"
        fill="none"
      >
        <path
          d="M3 7l3 3 5-5"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
        />
      </svg>
      <svg
        v-else-if="indeterminate"
        class="s-checkbox__icon"
        viewBox="0 0 14 14"
        fill="none"
      >
        <path
          d="M3 7h8"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
        />
      </svg>
    </span>
    <span
      v-if="$slots.default"
      class="s-checkbox__label"
    >
      <slot />
    </span>
  </label>
</template>

<style scoped>
.s-checkbox {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  min-height: 44px;
}

.s-checkbox--disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.s-checkbox__native {
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

.s-checkbox__box {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  flex-shrink: 0;
  border: 1.5px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-bg);
  transition:
    background var(--transition-fast),
    border-color var(--transition-fast);
}

.s-checkbox__box--checked,
.s-checkbox__box--indeterminate {
  background: var(--color-accent);
  border-color: var(--color-accent);
  color: #fff;
}

.s-checkbox__native:focus-visible ~ .s-checkbox__box {
  box-shadow: var(--focus-ring);
}

.s-checkbox__icon {
  width: 14px;
  height: 14px;
}

.s-checkbox__label {
  font-size: 0.875rem;
  color: var(--color-fg);
  user-select: none;
}
</style>
