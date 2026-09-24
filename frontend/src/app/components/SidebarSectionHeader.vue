<script setup lang="ts">
defineProps<{ label: string }>()
</script>

<template>
  <div class="section-header">
    {{ label }}
  </div>
</template>

<style scoped>
.section-header {
  text-transform: uppercase;
  font-size: 11px;
  font-weight: var(--weight-semibold);
  color: var(--color-sidebar-section-text);
  padding: var(--space-4) var(--space-4) var(--space-2);
  letter-spacing: 0.05em;
}
</style>
