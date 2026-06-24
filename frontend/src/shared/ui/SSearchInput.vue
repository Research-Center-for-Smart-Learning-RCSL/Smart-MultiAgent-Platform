<script setup lang="ts">
import { computed } from 'vue'
import { MagnifyingGlassIcon, XMarkIcon } from '@heroicons/vue/20/solid'

const props = withDefaults(defineProps<{
  modelValue: string
  placeholder?: string
  loading?: boolean
}>(), {
  placeholder: undefined,
  loading: false,
})

const emit = defineEmits<{
  'update:modelValue': [value: string]
  search: []
  clear: []
}>()

const hasValue = computed(() => props.modelValue.length > 0)

function onInput(e: Event) {
  const target = e.target as HTMLInputElement
  emit('update:modelValue', target.value)
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter') {
    emit('search')
  }
}

function onClear() {
  emit('update:modelValue', '')
  emit('clear')
}
</script>

<template>
  <div class="search-input">
    <svg
      v-if="loading"
      class="search-input__spinner"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <circle
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        stroke-width="3"
        opacity="0.25"
      />
      <path
        d="M12 2a10 10 0 0 1 10 10"
        stroke="currentColor"
        stroke-width="3"
        stroke-linecap="round"
      />
    </svg>
    <MagnifyingGlassIcon
      v-else
      class="search-input__icon"
      aria-hidden="true"
    />
    <input
      class="search-input__field"
      type="text"
      :value="modelValue"
      :placeholder="placeholder"
      aria-label="Search"
      @input="onInput"
      @keydown="onKeydown"
    >
    <button
      v-if="hasValue"
      class="search-input__clear"
      type="button"
      aria-label="Clear search"
      @click="onClear"
    >
      <XMarkIcon
        class="search-input__clear-icon"
        aria-hidden="true"
      />
    </button>
  </div>
</template>

<style scoped>
.search-input {
  position: relative;
  display: flex;
  align-items: center;
  height: 40px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg);
  transition: border-color var(--transition-fast);
}

.search-input:focus-within {
  border-color: var(--color-accent);
  outline: var(--focus-ring);
}

.search-input__icon,
.search-input__spinner {
  position: absolute;
  left: 8px;
  width: 16px;
  height: 16px;
  color: var(--color-muted);
  pointer-events: none;
  flex-shrink: 0;
}

.search-input__spinner {
  animation: search-spin 0.8s linear infinite;
}

@keyframes search-spin {
  to {
    transform: rotate(360deg);
  }
}

.search-input__field {
  width: 100%;
  height: 100%;
  padding: 0 8px 0 32px;
  border: none;
  background: transparent;
  color: var(--color-fg);
  font-size: 0.875rem;
  outline: none;
}

.search-input__field::placeholder {
  color: var(--color-muted);
}

.search-input__clear {
  position: absolute;
  right: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-muted);
  cursor: pointer;
  transition: color var(--transition-fast), background var(--transition-fast);
}

.search-input__clear:hover {
  color: var(--color-fg);
  background: var(--color-surface);
}

.search-input__clear-icon {
  width: 16px;
  height: 16px;
}
</style>
