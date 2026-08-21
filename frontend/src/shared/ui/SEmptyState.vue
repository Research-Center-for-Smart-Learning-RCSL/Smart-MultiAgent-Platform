<script setup lang="ts">
import type { Component } from 'vue'

defineProps<{
  title?: string
  text?: string
  icon?: Component
}>()
</script>

<template>
  <div class="s-empty-state">
    <div
      v-if="icon"
      class="s-empty-state__halo"
      aria-hidden="true"
    >
      <component
        :is="icon"
        class="s-empty-state__icon"
      />
    </div>
    <p
      v-if="title"
      class="s-empty-state__title"
    >
      {{ title }}
    </p>
    <p
      v-if="text"
      class="s-empty-state__text"
    >
      {{ text }}
    </p>
    <slot />
    <div
      v-if="$slots.action"
      class="s-empty-state__action"
    >
      <slot name="action" />
    </div>
  </div>
</template>

<style scoped>
.s-empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  /* A no-op while the box is content-height, so this can only change rendering
     where a consumer stretches the component - which is the defect: a stretched
     instance had no main-axis rule and packed its content to the top. */
  justify-content: center;
  gap: var(--space-2);
  padding: var(--space-8) var(--space-4);
  text-align: center;
  max-width: 400px;
  margin: 0 auto;
}

/* Tinted halo lifts the icon out of "grey ghost" territory without shouting;
   the soft outer ring echoes the status-tint language used by badges. */
.s-empty-state__halo {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 72px;
  height: 72px;
  border-radius: var(--radius-full);
  background: var(--color-info-tint);
  box-shadow: 0 0 0 8px color-mix(in srgb, var(--color-info-tint) 40%, transparent);
  margin-bottom: var(--space-2);
}

.s-empty-state__icon {
  width: 32px;
  height: 32px;
  color: var(--color-info-on);
}

.s-empty-state__title {
  font-size: var(--font-size-md);
  font-weight: var(--weight-semibold);
  color: var(--color-fg);
  margin: 0;
}

.s-empty-state__text {
  font-size: var(--font-size-sm);
  color: var(--color-muted);
  margin: 0;
  line-height: var(--line-normal);
}

.s-empty-state__action {
  margin-top: var(--space-2);
}
</style>
