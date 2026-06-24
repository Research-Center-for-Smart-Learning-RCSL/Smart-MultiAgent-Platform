<script setup lang="ts">
import { ref, computed, useSlots } from 'vue'
import { EyeIcon, EyeSlashIcon } from '@heroicons/vue/20/solid'

const props = withDefaults(defineProps<{
  modelValue?: string | number
  type?: 'text' | 'password' | 'email' | 'number' | 'url'
  placeholder?: string
  disabled?: boolean
  error?: boolean
  size?: 'sm' | 'md'
  id?: string | undefined
}>(), {
  modelValue: '',
  type: 'text',
  placeholder: '',
  disabled: false,
  error: false,
  size: 'md',
  id: undefined,
})

const emit = defineEmits<{
  'update:modelValue': [value: string | number]
}>()

const slots = useSlots()

const passwordVisible = ref(false)

const internalType = computed(() => {
  if (props.type === 'password' && passwordVisible.value) return 'text'
  return props.type
})

const hasPrefix = computed(() => !!slots.prefix)
const hasSuffix = computed(() => !!slots.suffix)
const isPassword = computed(() => props.type === 'password')

function onInput(event: Event) {
  const target = event.target as HTMLInputElement
  const value = props.type === 'number' ? Number(target.value) : target.value
  emit('update:modelValue', value)
}

function togglePasswordVisibility() {
  passwordVisible.value = !passwordVisible.value
}
</script>

<template>
  <div
    class="s-input"
    :class="[
      `s-input--${size}`,
      {
        's-input--error': error,
        's-input--disabled': disabled,
        's-input--has-prefix': hasPrefix,
        's-input--has-suffix': hasSuffix || isPassword,
      },
    ]"
  >
    <span
      v-if="hasPrefix"
      class="s-input__prefix"
    >
      <slot name="prefix" />
    </span>
    <input
      :id="id"
      class="s-input__field"
      :type="internalType"
      :value="modelValue"
      :placeholder="placeholder"
      :disabled="disabled"
      @input="onInput"
    >
    <span
      v-if="hasSuffix || isPassword"
      class="s-input__suffix"
    >
      <slot name="suffix" />
      <button
        v-if="isPassword"
        type="button"
        class="s-input__eye-toggle"
        :disabled="disabled"
        tabindex="-1"
        @click="togglePasswordVisibility"
      >
        <EyeSlashIcon
          v-if="passwordVisible"
          class="s-input__eye-icon"
          aria-hidden="true"
        />
        <EyeIcon
          v-else
          class="s-input__eye-icon"
          aria-hidden="true"
        />
      </button>
    </span>
  </div>
</template>

<style scoped>
.s-input {
  display: flex;
  align-items: center;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg);
  transition: border-color var(--transition-fast);
}

.s-input:focus-within {
  outline: 2px solid var(--color-accent);
  outline-offset: -1px;
}

.s-input--error {
  border-color: var(--color-danger);
}

.s-input--error:focus-within {
  outline-color: var(--color-danger);
}

.s-input--disabled {
  opacity: 0.5;
  cursor: not-allowed;
  background: var(--color-surface);
}

/* -- Sizes -- */
.s-input--sm {
  min-height: 32px;
}

.s-input--md {
  min-height: 40px;
}

/* -- Field -- */
.s-input__field {
  flex: 1;
  min-width: 0;
  border: none;
  background: transparent;
  color: var(--color-fg);
  font-size: 0.875rem;
  padding: 0 8px;
  height: 100%;
  outline: none;
}

.s-input__field::placeholder {
  color: var(--color-muted);
}

.s-input__field:disabled {
  cursor: not-allowed;
}

.s-input--sm .s-input__field {
  font-size: 0.75rem;
}

/* -- Prefix / Suffix -- */
.s-input__prefix,
.s-input__suffix {
  display: flex;
  align-items: center;
  flex-shrink: 0;
  color: var(--color-muted);
  font-size: 0.875rem;
}

.s-input__prefix {
  padding-left: 8px;
}

.s-input__suffix {
  padding-right: 4px;
}

/* -- Password eye toggle -- */
.s-input__eye-toggle {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  min-height: 44px;
  min-width: 44px;
  margin: -6px -4px -6px 0;
  padding: 0;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-muted);
  cursor: pointer;
  transition: color var(--transition-fast);
}

.s-input__eye-toggle:hover {
  color: var(--color-fg);
}

.s-input__eye-toggle:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

.s-input__eye-icon {
  width: 18px;
  height: 18px;
}
</style>
