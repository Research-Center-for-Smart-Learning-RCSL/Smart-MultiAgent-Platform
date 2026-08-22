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
  /* A sheet, not the canvas, and deliberately so: the landing page is one
     continuous surface whose sections alternate against --color-surface bands.
     On the canvas that alternation would invert, since the canvas sits below
     the recessed fill it is meant to contrast with. */
  background: var(--color-bg);
  color: var(--color-fg);
}
</style>
