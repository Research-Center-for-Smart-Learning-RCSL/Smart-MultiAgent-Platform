<script setup lang="ts">
defineProps<{
  text?: string
  label?: string
  size?: 'sm' | 'md' | 'lg'
}>()
</script>

<template>
  <div
    class="s-spinner"
    role="status"
    :aria-label="label"
  >
    <svg
      class="s-spinner__icon"
      :class="`s-spinner__icon--${size ?? 'md'}`"
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
    <span v-if="text">{{ text }}</span>
    <span
      v-if="label && !text"
      class="visually-hidden"
    >{{ label }}</span>
  </div>
</template>

<style scoped>
.s-spinner {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  color: var(--color-muted);
  font-size: 0.875rem;
}

.s-spinner__icon {
  animation: s-spin 0.8s linear infinite;
}

.s-spinner__icon--sm {
  width: 16px;
  height: 16px;
}

.s-spinner__icon--md {
  width: 24px;
  height: 24px;
}

.s-spinner__icon--lg {
  width: 32px;
  height: 32px;
}

@keyframes s-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
