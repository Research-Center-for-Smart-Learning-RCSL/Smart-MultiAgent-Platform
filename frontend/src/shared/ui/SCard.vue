<script setup lang="ts">
defineProps<{
  variant?: 'default' | 'elevated' | 'bordered' | 'flat'
  padding?: 'none' | 'sm' | 'md' | 'lg'
}>()
</script>

<template>
  <div
    class="s-card"
    :class="[
      `s-card--${variant ?? 'default'}`,
      `s-card--pad-${padding ?? 'md'}`,
    ]"
  >
    <slot />
  </div>
</template>

<style scoped>
.s-card {
  border-radius: var(--radius-md);
}

.s-card--default {
  background: var(--color-bg);
  border: 1px solid var(--color-border);
}

.s-card--elevated {
  background: var(--color-bg);
  box-shadow: var(--shadow-sm);
}

.s-card--bordered {
  background: var(--color-bg);
  border: 1px solid var(--color-border);
}

.s-card--flat {
  background: var(--color-surface);
}

.s-card--pad-none { padding: 0; }
.s-card--pad-sm { padding: 12px; }
.s-card--pad-md { padding: 16px; }
.s-card--pad-lg { padding: 24px; }
</style>
