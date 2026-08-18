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
  // Whether the type listing failed, as opposed to coming back empty. The two
  // need different copy: one is an instruction the teacher can act on, the other
  // is not their doing.
  activityTypesFailed: boolean
  busy: boolean
}>()

const emit = defineEmits<{
  save: [granted: boolean, typeIds: string[]]
}>()

const { t } = useI18n()

const granted = ref(false)
const selected = ref<string[]>([])

/** A stored allowlist entry whose type this project can no longer use.
 *
 *  The server drops these at turn-assembly time rather than at write time, so the
 *  teacher's selection can outlive a deleted worksheet. Rendering the count is
 *  what turns "the agent quietly runs fewer activities than I picked" into
 *  something visible.
 *
 *  Zero when the listing failed, and that guard is the whole point of the flag: an
 *  empty `activityTypes` from a failure makes *every* stored entry look unresolved,
 *  so without it one network hiccup tells the teacher their entire selection has
 *  been deleted. A count is only meaningful against a list we actually have. */
const unresolvedCount = computed(() => {
  if (props.activityTypesFailed) return 0
  const known = new Set(props.activityTypes.map((a) => a.id))
  return (props.agent.activity_type_allowlist ?? []).filter((id) => !known.has(id)).length
})

/** The stored grant, reduced to the types this project can still use.
 *
 *  The narrowing is load-bearing, not cosmetic. An id the project can no longer
 *  reach has no checkbox, so seeding the draft with it would make it unremovable
 *  through this UI — and the grant route validates every id it is sent, so every
 *  later Apply would 422 with no way for the teacher to see which entry did it.
 *  Dropping it here means the next Apply also repairs the stored row, and
 *  `unresolvedCount` above is what says so rather than leaving it silent. */
const storedDraft = computed(() => {
  const known = new Set(props.activityTypes.map((a) => a.id))
  return {
    granted: props.agent.may_control_activities === true,
    typeIds: (props.agent.activity_type_allowlist ?? []).filter((id) => known.has(id)),
  }
})

// Re-seeded whenever the stored grant changes — including after a save, since the
// parent reloads the bindings rather than patching its local copy.
//
// Keyed on the *values*, not on `props.agent`'s identity: `boundAgents` rebuilds
// every element on each `loadBindings()`, so an identity watch would reset a draft
// the teacher is still filling in whenever some unrelated write (another agent's
// role, a bind, an unbind) reloads the panel.
watch(
  () => `${storedDraft.value.granted}|${storedDraft.value.typeIds.join(',')}`,
  () => {
    granted.value = storedDraft.value.granted
    selected.value = [...storedDraft.value.typeIds]
  },
  { immediate: true },
)

const dirty = computed(() => {
  // Nothing on this panel is actionable without the type list: the draft would be
  // narrowed to nothing, so Apply would emit `save(true, [])` and be refused by the
  // client guard for want of a selection the teacher was never offered. Checked
  // before the toggle comparison, because flipping the switch does not make an
  // unanswerable form answerable.
  if (props.activityTypesFailed) return false
  if (granted.value !== storedDraft.value.granted) return true
  // A revoke writes no allowlist — the server keeps the stored one so the
  // teacher's selection survives a re-grant — so while the draft is off there is
  // nothing Apply could act on, and enabling it would be a button that lies.
  if (!granted.value) return false
  // Same for a grant the teacher cannot express: an empty draft is refused
  // client-side, so offering Apply would promise a write that cannot happen.
  if (!selected.value.length) return false
  // Compared against the *raw* stored list, while the draft was seeded from the
  // narrowed one. The asymmetry is the point: a live grant still carrying an id
  // the project cannot use reads as dirty on load, so Apply is enabled and one
  // click rewrites the row without the dead entry. Comparing against the narrowed
  // list would agree with the draft, disable the button, and leave
  // `unresolvedCount`'s note pointing at something the teacher cannot act on.
  const stored = [...(props.agent.activity_type_allowlist ?? [])].sort()
  const draft = [...selected.value].sort()
  return stored.length !== draft.length || stored.some((id, i) => id !== draft[i])
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
        v-if="activityTypesFailed"
        class="access-row__desc activity-control__warn"
        role="alert"
      >
        {{ t('conversation.activityControl.typesLoadFailed') }}
      </p>
      <p
        v-else-if="!activityTypes.length"
        class="access-row__desc activity-control__warn"
      >
        {{ t('conversation.activityControl.noTypes') }}
      </p>
      <fieldset
        v-if="activityTypes.length"
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
