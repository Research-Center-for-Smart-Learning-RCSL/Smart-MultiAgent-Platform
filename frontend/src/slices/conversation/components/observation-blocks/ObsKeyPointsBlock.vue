<template>
  <ObsBlockFrame
    :title="block.title"
    :caveat="block.caveat"
    :basis="block.basis"
  >
    <ul class="obs-points">
      <li
        v-for="(point, i) in block.points"
        :key="i"
        class="obs-points__item"
      >
        <!-- Text nodes, never markup: every string here is model-authored and
             the model reads participant text. No `v-html` on this path. -->
        <span>{{ point.text }}</span>
        <span
          v-if="point.evidence"
          class="obs-points__evidence"
        >{{ point.evidence }}</span>
      </li>
    </ul>

    <p
      v-if="block.next_step"
      class="obs-points__next"
    >
      <span class="obs-points__next-label">{{ t('conversation.observers.blocks.nextStep') }}</span>
      {{ block.next_step }}
    </p>
  </ObsBlockFrame>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import ObsBlockFrame from './ObsBlockFrame.vue'
import type { ObservationKeyPointsBlock } from '../../types'

defineProps<{ block: ObservationKeyPointsBlock }>()

const { t } = useI18n()
</script>

<style scoped>
.obs-points {
  list-style: disc;
  margin: 0;
  padding-left: var(--space-5);
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  font-size: var(--font-size-code);
  color: var(--color-fg);
}

.obs-points__item {
  overflow-wrap: anywhere;
}

.obs-points__evidence {
  display: block;
  font-size: var(--font-size-xs);
  color: var(--color-muted);
}

.obs-points__next {
  margin: 0;
  font-size: var(--font-size-code);
  color: var(--color-fg);
  overflow-wrap: anywhere;
}

.obs-points__next-label {
  font-weight: var(--weight-semibold);
}
</style>
