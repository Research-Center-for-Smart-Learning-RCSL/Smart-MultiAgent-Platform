<template>
  <STooltip :content="t('shared.draftDisclosure.tooltip')">
    <span class="draft-chip">
      <PencilSquareIcon class="draft-chip__icon" />
      {{ t('shared.draftDisclosure.chip') }}
    </span>
  </STooltip>
</template>

<script setup lang="ts">
/**
 * "An agent here can read what you are typing" ([R32.05]).
 *
 * In `@shared/ui` rather than beside `ObserverDisclosureChip.vue`, which it is
 * shaped after, because it is rendered by BOTH the conversation slice (on the
 * composer) and the activities slice (on the worksheet) — and gate #1 forbids
 * `activities` importing `conversation` back. A duplicated copy would be worse
 * than a shared one: the two surfaces must say the same thing, and the whole
 * risk this chip addresses is a participant who was told about one surface and
 * not the other.
 *
 * It names no agent. Which binding holds the grant is the room creator's to see
 * ([R28.10], [R32.05]); what a participant is owed is that it is happening.
 *
 * Content-free by construction: it takes no props, so there is no path by which
 * a draft, a code or an agent name could reach it.
 */
import { useI18n } from 'vue-i18n'
import { PencilSquareIcon } from '@heroicons/vue/24/outline'
import STooltip from './STooltip.vue'

const { t } = useI18n()
</script>

<style scoped>
/* Deliberately the same geometry and weight as ObserverDisclosureChip: the two
   appear in the same room and a participant should read them as one class of
   notice rather than as one being louder than the other. */
.draft-chip {
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

.draft-chip__icon {
  width: 12px;
  height: 12px;
}
</style>
