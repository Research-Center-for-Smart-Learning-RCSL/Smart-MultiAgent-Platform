<script setup lang="ts">
defineProps<{
  text?: string
}>()
</script>

<template>
  <div
    class="loading-spinner"
    role="status"
  >
    <svg
      class="loading-spinner__icon"
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
  </div>
</template>

<style scoped>
.loading-spinner {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  color: var(--color-muted);
  font-size: 0.875rem;
  padding: 1rem 0;
}
.loading-spinner__icon {
  width: 1.25rem;
  height: 1.25rem;
  animation: spin 0.8s linear infinite;
}
@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
