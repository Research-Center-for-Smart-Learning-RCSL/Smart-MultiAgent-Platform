<script setup lang="ts">
// Compact constellation glyph echoing the hero: a hub linked to three nodes.
// Inherits the current text color, so callers set the tint. Decorative — the
// adjacent "SMAP" wordmark carries the accessible name.
</script>

<template>
  <svg
    class="brand-mark"
    viewBox="0 0 24 24"
    role="presentation"
    aria-hidden="true"
    focusable="false"
  >
    <line
      x1="12"
      y1="12"
      x2="12"
      y2="4"
    />
    <line
      x1="12"
      y1="12"
      x2="5"
      y2="18"
    />
    <line
      x1="12"
      y1="12"
      x2="19"
      y2="18"
    />
    <circle
      cx="12"
      cy="4"
      r="2"
    />
    <circle
      cx="5"
      cy="18"
      r="2"
    />
    <circle
      cx="19"
      cy="18"
      r="2"
    />
    <circle
      class="brand-mark__hub"
      cx="12"
      cy="12"
      r="3.2"
    />
  </svg>
</template>

<style scoped>
.brand-mark {
  width: 1.4em;
  height: 1.4em;
  display: block;
}

.brand-mark line {
  stroke: currentColor;
  stroke-width: 1.5;
  opacity: 0.55;
}

.brand-mark circle {
  fill: var(--color-bg);
  stroke: currentColor;
  stroke-width: 1.5;
}

.brand-mark__hub {
  fill: currentColor;
}
</style>
