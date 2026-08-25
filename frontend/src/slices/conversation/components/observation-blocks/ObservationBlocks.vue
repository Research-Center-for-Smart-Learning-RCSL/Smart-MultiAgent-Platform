<template>
  <div class="obs-blocks">
    <template
      v-for="(block, i) in blocks"
      :key="i"
    >
      <!-- `prose` is the one kind with a markdown body, and it may sit at any
           position in the array. It cannot render its own sanitised HTML: the
           gate #4 `v-html` allowlist holds exactly one file in this slice's
           observation path, so the card passes its binding down as a scoped slot
           and the markdown is rendered there, wherever the slot lands. -->
      <slot
        v-if="block.kind === 'prose'"
        name="prose"
        :block="block"
      />
      <ObsKeyPointsBlock
        v-else-if="block.kind === 'key_points'"
        :block="block"
      />
      <ObsTimelineBlock
        v-else-if="block.kind === 'timeline'"
        :block="block"
      />
      <ObsFieldCoverageBlock
        v-else-if="block.kind === 'field_coverage'"
        :block="block"
      />
      <ObsMandalaGridBlock
        v-else-if="block.kind === 'mandala_grid'"
        :block="block"
      />
      <ObsAttemptTableBlock
        v-else-if="block.kind === 'attempt_table'"
        :block="block"
      />
      <!-- Exhaustive above, so TypeScript narrows this branch to `never`. It is
           still reachable at runtime: `blocks` is JSONB the server stored, and a
           row written by a newer release lands here (AC-13). -->
      <ObsUnknownBlock
        v-else
        :block="block as unknown as Record<string, unknown>"
      />
    </template>
  </div>
</template>

<script setup lang="ts">
import ObsAttemptTableBlock from './ObsAttemptTableBlock.vue'
import ObsFieldCoverageBlock from './ObsFieldCoverageBlock.vue'
import ObsKeyPointsBlock from './ObsKeyPointsBlock.vue'
import ObsMandalaGridBlock from './ObsMandalaGridBlock.vue'
import ObsTimelineBlock from './ObsTimelineBlock.vue'
import ObsUnknownBlock from './ObsUnknownBlock.vue'
import type { ObservationBlock } from '../../types'

defineProps<{ blocks: ObservationBlock[] }>()

defineSlots<{
  prose(props: { block: ObservationBlock }): unknown
}>()
</script>

<style scoped>
.obs-blocks {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
</style>
