<template>
  <!-- Downward is a UX choice, not a clipping fix (the teleported tooltip
       clips nowhere): the chip sits in the room header, and an upward bubble
       would float over the app topbar above it instead of over the room's
       own content. -->
  <STooltip
    :content="t('conversation.observers.disclosureTooltip')"
    placement="bottom"
  >
    <span class="obs-chip">
      <EyeIcon class="obs-chip__icon" />
      {{ t('conversation.observers.disclosureChip') }}
    </span>
  </STooltip>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { EyeIcon } from '@heroicons/vue/24/outline'
import { STooltip } from '@shared/ui'

const { t } = useI18n()
</script>

<style scoped>
.obs-chip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: var(--space-0-5) var(--space-2-5);
  border-radius: var(--radius-full);
  font-size: var(--font-size-xs);
  color: var(--color-muted);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
}

.obs-chip__icon {
  width: 12px;
  height: 12px;
}
</style>
