<script setup lang="ts">
// Letting one bound agent read this room's unsent text ([R32.03]).
//
// Its own component beside `AgentActivityControl` rather than a row inside it,
// because it is a different authority with a different meaning, written by a
// different route. Folding them together would make one Apply express two
// decisions and make the audit trail ambiguous about which one a teacher took.
//
// **No draft-and-Apply here**, unlike its neighbour, and the difference is not an
// oversight. That panel batches because a grant and its allowlist are two halves
// of one state the server accepts or refuses together. This is a single switch:
// there is nothing to fill in alongside it, so an Apply step would add a click
// and a "did I save that?" without buying anything.
//
// **The confirm is the point.** Every other toggle in this view changes who can
// see something already written down. This one starts a machine reading text
// nobody has chosen to send, so it asks once, in words, before it does.
//
// Creator-only by construction: the parent renders it only for the room creator,
// and the server omits `may_read_drafts` from everyone else's listing, so a
// non-creator has nothing to read from ([R28.10], [R32.05]).

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { SToggle } from '@shared/ui'
import { useConfirmDialog } from '@shared/composables/useConfirmDialog'
import type { BoundAgent } from '../composables/useChatroomBindings'

const props = defineProps<{
  agent: BoundAgent
  busy: boolean
  /** Whether the room currently tells participants this is happening. Shown here
   *  as well as on the room's own toggle, because the moment a teacher grants the
   *  authority is the moment the disclosure setting actually matters. */
  disclosed: boolean
}>()

const emit = defineEmits<{
  save: [granted: boolean]
}>()

const { t } = useI18n()
const { confirm } = useConfirmDialog()

const granted = computed(() => props.agent.may_read_drafts === true)

async function onToggle(next: boolean): Promise<void> {
  if (next) {
    const ok = await confirm({
      title: t('conversation.draftAccess.confirmTitle'),
      message: props.disclosed
        ? t('conversation.draftAccess.confirmBody')
        : t('conversation.draftAccess.confirmBodyUndisclosed'),
      confirmLabel: t('conversation.draftAccess.confirmAction'),
      cancelLabel: t('app.cancel'),
      variant: 'warning',
    })
    if (!ok) return
  }
  // A revoke is never confirmed: taking authority away is the safe direction, and
  // a confirm there would put friction on the wrong side of the decision.
  emit('save', next)
}
</script>

<template>
  <div class="draft-access">
    <SToggle
      :model-value="granted"
      size="sm"
      :disabled="busy"
      @update:model-value="onToggle"
    >
      {{ t('conversation.draftAccess.label') }}
    </SToggle>
    <p class="access-row__desc">
      {{ t('conversation.draftAccess.help') }}
    </p>
    <p
      v-if="granted && !disclosed"
      class="access-row__desc draft-access__warn"
    >
      {{ t('conversation.draftAccess.undisclosedNote') }}
    </p>
  </div>
</template>

<style scoped>
.draft-access {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  align-items: flex-start;
}
.draft-access__warn {
  color: var(--color-warning);
}
</style>
