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
    <component
      :is="icon"
      v-if="icon"
      class="s-empty-state__icon"
      aria-hidden="true"
    />
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
  gap: 8px;
  padding: 2rem 1rem;
  text-align: center;
  max-width: 400px;
  margin: 0 auto;
}

.s-empty-state__icon {
  width: 48px;
  height: 48px;
  color: var(--color-muted);
}

.s-empty-state__title {
  font-size: 1rem;
  font-weight: 500;
  color: var(--color-fg);
  margin: 0;
}

.s-empty-state__text {
  font-size: 0.875rem;
  color: var(--color-muted);
  margin: 0;
  line-height: 1.5;
}

.s-empty-state__action {
  margin-top: 8px;
}
</style>
