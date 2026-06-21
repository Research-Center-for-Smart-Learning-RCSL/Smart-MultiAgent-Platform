<script setup lang="ts">
defineProps<{
  padding?: 'compact' | 'default'
  variant?: 'default' | 'surface'
}>()
</script>

<template>
  <div
    class="rounded-md border border-border"
    :class="[
      (padding ?? 'default') === 'compact' ? 'p-3' : 'p-4',
      (variant ?? 'default') === 'surface' ? 'bg-surface' : 'bg-bg',
    ]"
  >
    <slot />
  </div>
</template>
