<script setup lang="ts">
const props = withDefaults(defineProps<{
  modelValue?: boolean
  disabled?: boolean
  size?: 'sm' | 'md'
  id?: string | undefined
}>(), {
  modelValue: false,
  disabled: false,
  size: 'md',
  id: undefined,
})

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

function toggle() {
  if (props.disabled) return
  emit('update:modelValue', !props.modelValue)
}
</script>

<template>
  <div
    class="s-toggle"
    :class="{ 's-toggle--disabled': disabled }"
  >
    <button
      :id="id"
      type="button"
      role="switch"
      :aria-checked="modelValue"
      :disabled="disabled"
      class="s-toggle__track"
      :class="[
        `s-toggle__track--${size}`,
        { 's-toggle__track--on': modelValue },
      ]"
      @click="toggle"
    >
      <span
        class="s-toggle__knob"
        :class="[
          `s-toggle__knob--${size}`,
          { 's-toggle__knob--on': modelValue },
        ]"
        aria-hidden="true"
      />
    </button>
    <span
      v-if="$slots.default"
      class="s-toggle__label"
    >
      <slot />
    </span>
  </div>
</template>

<style scoped>
.s-toggle {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 44px;
}

.s-toggle--disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.s-toggle__track {
  position: relative;
  display: flex;
  align-items: center;
  border: 1.5px solid color-mix(in srgb, var(--color-border), var(--color-fg) 12%);
  border-radius: var(--radius-full);
  background: var(--color-border);
  cursor: pointer;
  padding: 0;
  transition:
    background var(--transition-fast),
    border-color var(--transition-fast);
}

.s-toggle__track--md {
  width: 36px;
  height: 20px;
}

.s-toggle__track--sm {
  width: 28px;
  height: 16px;
}

.s-toggle__track--on {
  background: var(--color-accent);
  border-color: var(--color-accent);
}

.s-toggle__track:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

.s-toggle__track:disabled {
  cursor: not-allowed;
}

.s-toggle__knob {
  position: absolute;
  border-radius: var(--radius-full);
  background: #fff;
  box-shadow: var(--shadow-sm);
  transition: transform var(--transition-fast);
}

.s-toggle__knob--md {
  width: 14px;
  height: 14px;
  left: 2px;
}

.s-toggle__knob--md.s-toggle__knob--on {
  transform: translateX(16px);
}

.s-toggle__knob--sm {
  width: 10px;
  height: 10px;
  left: 2px;
}

.s-toggle__knob--sm.s-toggle__knob--on {
  transform: translateX(12px);
}

.s-toggle__label {
  font-size: 0.875rem;
  color: var(--color-fg);
  user-select: none;
}
</style>
