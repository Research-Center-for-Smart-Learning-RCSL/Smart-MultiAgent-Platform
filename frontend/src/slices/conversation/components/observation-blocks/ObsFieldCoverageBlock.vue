<template>
  <ObsBlockFrame
    :title="block.title"
    :caveat="block.caveat"
    :basis="block.basis"
    :counted="block.submissions_counted"
  >
    <ul class="obs-coverage">
      <li
        v-for="cell in block.cells"
        :key="cell.name"
        class="obs-coverage__row"
      >
        <span class="obs-coverage__label">{{ cell.title || cell.name }}</span>
        <!-- A bar whose width is the field's share of the *counted submissions*,
             which is the denominator printed below it. Deliberately no percentage
             text: a number beside a bar reads as a rate, and this data cannot
             support one ([R28.18]). -->
        <span
          class="obs-coverage__bar"
          aria-hidden="true"
        >
          <span
            class="obs-coverage__fill"
            :style="{ width: widthOf(cell.filled) }"
          />
        </span>
        <span class="obs-coverage__count">{{ cell.filled }}</span>
      </li>
    </ul>
  </ObsBlockFrame>
</template>

<script setup lang="ts">
import ObsBlockFrame from './ObsBlockFrame.vue'
import type { ObservationFieldCoverageBlock } from '../../types'

const props = defineProps<{ block: ObservationFieldCoverageBlock }>()

function widthOf(filled: number): string {
  const total = props.block.submissions_counted
  if (!total || total <= 0) return '0%'
  // Clamped rather than trusted: the aggregate cannot exceed its own
  // denominator, but a bar wider than its track would break the row's layout if
  // a future kind ever reused this shape with a different pair.
  return `${Math.min(100, Math.max(0, (filled / total) * 100))}%`
}
</script>

<style scoped>
.obs-coverage {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.obs-coverage__row {
  display: grid;
  /* minmax(0, ...) on the label track: a `1fr` track is floored at min-content,
     so a long field title would size the row rather than wrap inside it. */
  grid-template-columns: minmax(0, 1fr) 64px auto;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--font-size-xs);
}

.obs-coverage__label {
  color: var(--color-fg);
  overflow-wrap: anywhere;
}

.obs-coverage__bar {
  display: block;
  height: 6px;
  border-radius: var(--radius-full);
  background: var(--color-neutral-tint);
  overflow: hidden;
}

.obs-coverage__fill {
  display: block;
  height: 100%;
  background: var(--color-accent);
}

.obs-coverage__count {
  color: var(--color-muted);
  font-variant-numeric: tabular-nums;
}
</style>
