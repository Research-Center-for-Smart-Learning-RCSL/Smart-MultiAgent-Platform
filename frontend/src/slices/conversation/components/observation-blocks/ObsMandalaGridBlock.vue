<template>
  <ObsBlockFrame
    :title="block.title"
    :caveat="block.caveat"
    :basis="block.basis"
    :counted="block.submissions_counted"
  >
    <div class="obs-grid">
      <template
        v-for="(row, r) in block.rows"
        :key="r"
      >
        <div
          v-for="cell in row"
          :key="cell.name"
          class="obs-grid__cell"
        >
          <span class="obs-grid__label">{{ cell.title || cell.name }}</span>
          <span class="obs-grid__count">{{ cell.filled }}</span>
        </div>
      </template>
    </div>
  </ObsBlockFrame>
</template>

<script setup lang="ts">
import ObsBlockFrame from './ObsBlockFrame.vue'
import type { ObservationMandalaGridBlock } from '../../types'

defineProps<{ block: ObservationMandalaGridBlock }>()
</script>

<style scoped>
/* Position is the whole meaning here: the cells sit where the worksheet's own
   nine boxes sit, so the grid is fixed at three columns and the server refuses
   to build one for a type of any other width. */
.obs-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-1);
}

.obs-grid__cell {
  display: flex;
  flex-direction: column;
  gap: var(--space-0-5);
  padding: var(--space-1-5);
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  min-width: 0;
}

.obs-grid__label {
  font-size: var(--font-size-xs);
  color: var(--color-muted);
  overflow-wrap: anywhere;
}

.obs-grid__count {
  font-size: var(--font-size-code);
  font-weight: var(--weight-semibold);
  color: var(--color-fg);
  font-variant-numeric: tabular-nums;
}
</style>
