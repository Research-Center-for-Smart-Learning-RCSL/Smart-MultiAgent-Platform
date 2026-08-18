<script setup lang="ts">
// Delegating activity start/end authority to one bound agent ([R30.37]).
//
// A local draft plus an explicit Apply, rather than writing on every click: the
// toggle and the checkboxes are two halves of one decision the server accepts or
// refuses together (granting with an empty allowlist is a 422 and a DB CHECK), so
// applying them separately would produce a refusal for a state the teacher was
// halfway through expressing.
//
// Creator-only by construction: the parent renders this only for the room
// creator, and the server omits `may_control_activities` from everyone else's
// listing, so a non-creator has nothing to draft from ([R28.10]).

import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { SButton, SCheckbox, SToggle } from '@shared/ui'
import type { ActivityType } from '@slices/activities'
import type { BoundAgent } from '../composables/useChatroomBindings'

const props = defineProps<{
  agent: BoundAgent
  activityTypes: ActivityType[]
  busy: boolean
}>()

const emit = defineEmits<{
  save: [granted: boolean, typeIds: string[]]
}>()

const { t } = useI18n()

const granted = ref(false)
const selected = ref<string[]>([])

// Re-seeded whenever the stored grant changes — including after a save, since the
// parent reloads the bindings rather than patching its local copy. Seeding from a
// fresh array keeps the draft from aliasing the stored one.
watch(
  () => [props.agent.may_control_activities, props.agent.activity_type_allowlist] as const,
  ([stored, allowlist]) => {
    granted.value = stored === true
    selected.value = [...(allowlist ?? [])]
  },
  { immediate: true },
)

/** A stored allowlist entry whose type this project can no longer use.
 *
 *  The server drops these at turn-assembly time rather than at write time, so the
 *  teacher's selection can outlive a deleted worksheet. Rendering the count is
 *  what turns "the agent quietly runs fewer activities than I picked" into
 *  something visible. */
const unresolvedCount = computed(() => {
  const known = new Set(props.activityTypes.map((a) => a.id))
  return (props.agent.activity_type_allowlist ?? []).filter((id) => !known.has(id)).length
})

const dirty = computed(() => {
  const storedGranted = props.agent.may_control_activities === true
  const stored = [...(props.agent.activity_type_allowlist ?? [])].sort()
  const draft = [...selected.value].sort()
  return (
    granted.value !== storedGranted ||
    stored.length !== draft.length ||
    stored.some((id, i) => id !== draft[i])
  )
})

const showObserverNote = computed(() => granted.value && props.agent.role === 'observer')

function toggleType(typeId: string, checked: boolean): void {
  selected.value = checked
    ? [...selected.value, typeId]
    : selected.value.filter((id) => id !== typeId)
}
</script>

<template>
  <div class="activity-control">
    <SToggle
      v-model="granted"
      size="sm"
      :disabled="busy"
    >
      {{ t('conversation.activityControl.label') }}
    </SToggle>
    <p class="access-row__desc">
      {{ t('conversation.activityControl.help') }}
    </p>

    <template v-if="granted">
      <p
        v-if="!activityTypes.length"
        class="access-row__desc activity-control__warn"
      >
        {{ t('conversation.activityControl.noTypes') }}
      </p>
      <fieldset
        v-else
        class="activity-control__types"
      >
        <legend class="activity-control__legend">
          {{ t('conversation.activityControl.allowlist') }}
        </legend>
        <SCheckbox
          v-for="activityType in activityTypes"
          :key="activityType.id"
          :model-value="selected.includes(activityType.id)"
          :disabled="busy"
          @update:model-value="(v) => toggleType(activityType.id, v)"
        >
          {{ activityType.name }}
        </SCheckbox>
      </fieldset>

      <p
        v-if="unresolvedCount"
        class="access-row__desc activity-control__warn"
      >
        {{ t('conversation.activityControl.unresolved', { count: unresolvedCount }) }}
      </p>

      <p
        v-if="showObserverNote"
        class="access-row__desc activity-control__warn"
      >
        {{ t('conversation.activityControl.observerNote') }}
      </p>
    </template>

    <SButton
      variant="secondary"
      size="sm"
      :disabled="busy || !dirty"
      @click="emit('save', granted, selected)"
    >
      {{ t('conversation.activityControl.apply') }}
    </SButton>
  </div>
</template>

<style scoped>
.activity-control {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  align-items: flex-start;
}
.activity-control__types {
  display: flex;
  flex-direction: column;
  border: none;
  margin: 0;
  padding: 0;
}
.activity-control__legend {
  font-size: var(--font-size-sm);
  color: var(--color-muted);
  padding: 0;
}
.activity-control__warn {
  color: var(--color-warning, var(--color-muted));
}
</style>
