<script setup lang="ts">
defineProps<{
  text: string
}>()
</script>

<template>
  <div class="empty-state">
    <p>{{ text }}</p>
    <slot />
  </div>
</template>

<style scoped>
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.5rem;
  padding: 2rem 1rem;
  color: var(--color-muted);
  font-size: 0.875rem;
  text-align: center;
}
.empty-state p {
  margin: 0;
}
</style>
