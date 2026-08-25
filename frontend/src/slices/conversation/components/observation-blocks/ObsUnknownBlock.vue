<template>
  <!-- AC-13. A stored observation must survive a rollback of this frontend past
       the release that introduced its block kinds, so an unrecognised kind is a
       rendered line rather than a thrown render: the other blocks in the array
       still reach the creator. -->
  <section class="obs-unknown">
    <h4
      v-if="title"
      class="obs-unknown__title"
    >
      {{ title }}
    </h4>
    <p class="obs-unknown__text">
      {{ t('conversation.observers.blocks.cannotDisplay') }}
    </p>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps<{ block: Record<string, unknown> }>()

const { t } = useI18n()

const title = computed(() => {
  const value = props.block?.title
  return typeof value === 'string' ? value : ''
})
</script>

<style scoped>
.obs-unknown {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  padding: var(--space-2);
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-sm);
}

.obs-unknown__title {
  margin: 0;
  font-size: var(--font-size-code);
  font-weight: var(--weight-semibold);
  color: var(--color-fg);
}

.obs-unknown__text {
  margin: 0;
  font-size: var(--font-size-xs);
  color: var(--color-muted);
}
</style>
