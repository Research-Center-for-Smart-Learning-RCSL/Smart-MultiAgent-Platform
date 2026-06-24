<script setup lang="ts">
defineProps<{ value: string }>()
</script>

<template>
  <code class="text-[13px] font-mono text-[var(--color-muted)]">{{ value }}</code>
</template>
