<script setup lang="ts">
// Full-bleed passthrough for unauthenticated marketing pages (the landing).
// Unlike AuthLayout it adds no card chrome — the page owns its own nav/footer.
</script>

<template>
  <div class="public-layout">
    <slot />
  </div>
</template>

<style scoped>
.public-layout {
  min-height: 100dvh;
  background: var(--color-bg);
  color: var(--color-fg);
}
</style>
