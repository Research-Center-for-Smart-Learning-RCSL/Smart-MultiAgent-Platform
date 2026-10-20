<script setup lang="ts">
import type { Component } from 'vue'
import SCard from './SCard.vue'

withDefaults(
  defineProps<{
    label: string
    value: string | number
    hint?: string
    icon?: Component
    tone?: 'neutral' | 'success' | 'warning' | 'danger'
    compact?: boolean
  }>(),
  { tone: 'neutral', compact: false },
)
</script>

<template>
  <SCard
    class="s-stat-tile"
    :class="[`s-stat-tile--${tone}`, { 's-stat-tile--compact': compact }]"
    :variant="compact ? 'flat' : 'default'"
    :padding="compact ? 'none' : 'md'"
  >
    <component
      :is="icon"
      v-if="icon"
      class="s-stat-tile__icon"
      aria-hidden="true"
    />
    <div class="s-stat-tile__content">
      <span class="s-stat-tile__value">
        <slot name="value">{{ value }}</slot>
      </span>
      <span class="s-stat-tile__label">{{ label }}</span>
      <span
        v-if="hint"
        class="s-stat-tile__hint"
      >{{ hint }}</span>
    </div>
  </SCard>
</template>

<style scoped>
.s-stat-tile {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--space-2);
}

.s-card.s-stat-tile--compact {
  background: transparent;
  border: 0;
  box-shadow: none;
}

.s-stat-tile__icon {
  width: 1.5rem;
  height: 1.5rem;
  flex: none;
  color: var(--stat-tone, var(--color-muted));
}

.s-stat-tile__content {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: var(--space-1);
}

.s-stat-tile__value {
  color: var(--stat-tone, var(--color-fg));
  font-size: var(--font-size-2xl);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
  line-height: var(--line-tight);
}

.s-stat-tile__label,
.s-stat-tile__hint {
  color: var(--color-muted);
  font-size: var(--font-size-xs);
}

.s-stat-tile--success {
  --stat-tone: var(--color-success);
}

.s-stat-tile--warning {
  --stat-tone: var(--color-warning);
}

.s-stat-tile--danger {
  --stat-tone: var(--color-danger);
}

.s-stat-tile--compact .s-stat-tile__value {
  font-size: var(--font-size-md);
}
</style>
