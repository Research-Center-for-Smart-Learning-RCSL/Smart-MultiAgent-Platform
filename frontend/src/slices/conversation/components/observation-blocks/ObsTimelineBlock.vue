<template>
  <ObsBlockFrame
    :title="block.title"
    :caveat="block.caveat"
    :basis="block.basis"
  >
    <ol class="obs-timeline">
      <li
        v-for="(entry, i) in block.entries"
        :key="i"
        class="obs-timeline__item"
      >
        <span class="obs-timeline__label">{{ entry.label }}</span>
        <span
          v-if="entry.detail"
          class="obs-timeline__detail"
        >{{ entry.detail }}</span>
      </li>
    </ol>
  </ObsBlockFrame>
</template>

<script setup lang="ts">
import ObsBlockFrame from './ObsBlockFrame.vue'
import type { ObservationTimelineBlock } from '../../types'

defineProps<{ block: ObservationTimelineBlock }>()
</script>

<style scoped>
.obs-timeline {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1-5);
}

.obs-timeline__item {
  border-left: 2px solid var(--color-border);
  padding-left: var(--space-2);
  overflow-wrap: anywhere;
}

.obs-timeline__label {
  display: block;
  font-size: var(--font-size-code);
  font-weight: var(--weight-medium);
  color: var(--color-fg);
}

.obs-timeline__detail {
  display: block;
  font-size: var(--font-size-xs);
  color: var(--color-muted);
}
</style>
