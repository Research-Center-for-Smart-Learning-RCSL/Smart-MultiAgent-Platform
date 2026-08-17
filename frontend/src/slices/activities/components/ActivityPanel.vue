<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ApiError } from '@shared/errors'
import { SButton, SEmptyState, SSelect } from '@shared/ui'
import { useSessionStore } from '@shared/stores/session'
import {
  endActivation,
  getActiveActivation,
  getOwnRoundSession,
  getRoomActivityType,
  listActivityTypes,
  setActivationCompletion,
  startActivation,
} from '../api'
import { useActivationProgress } from '../composables/useActivationProgress'
import { usePolicyRefusal } from '../composables/usePolicyRefusal'
import { useActivitiesStore } from '../stores/activities'
import type { ActivityType, ActivityTypePublic } from '../types'
import ActivityHost from './ActivityHost.vue'

const props = defineProps<{
  chatroomId: string
  projectId?: string | undefined
  isCreator: boolean
}>()

const { t } = useI18n()
const { isPolicyRefusal, refusedFieldLabel } = usePolicyRefusal()
const session = useSessionStore()
const store = useActivitiesStore()
const types = ref<ActivityType[]>([])
const selectedTypeId = ref<string | null>(null)
const loading = ref(false)
const actionPending = ref(false)
const errorMessage = ref<string | null>(null)
// Fallback fetch when the activation carries no embedded type — a missed
// broadcast or a store reset (Q-1/AC-6). Keyed so a stale fetch for a since-
// ended activation is never rendered.
const fetchedType = ref<ActivityTypePublic | null>(null)
// The participant's own "I am finished" declaration for the current round; the
// facilitator's counts are the composable below. Per-activation, so it resets in
// the activation watcher.
const completed = ref(false)

const activation = computed(() => store.getActivation(props.chatroomId) ?? null)
const { progress } = useActivationProgress({
  chatroomId: () => props.chatroomId,
  isCreator: () => props.isCreator,
  activationId: () => activation.value?.id ?? null,
  startedByUserId: () => activation.value?.startedByUserId ?? null,
  viewerUserId: () => session.me?.id ?? null,
})
const activeType = computed(() => activation.value?.activityType ?? fetchedType.value)
// A project's usable set can hold its own type and an opted-in platform type
// under one key ([R30.02]), and `name` alone leaves those two indistinguishable
// here. The value is already the id, so starting the right one is possible —
// what was missing was any way to tell which is which. SSelect renders plain
// string labels, so the marker is a suffix rather than the SBadge the types
// table uses.
const typeOptions = computed(() =>
  types.value.map((activityType) => ({
    value: activityType.id,
    label:
      activityType.scope === 'platform'
        ? t('activities.panel.platformTypeOption', { name: activityType.name })
        : activityType.name,
  })),
)

async function hydrate(): Promise<void> {
  loading.value = true
  errorMessage.value = null
  try {
    const active = await getActiveActivation(props.chatroomId)
    if (store.getActivation(props.chatroomId) !== undefined) return
    if (active) store.setActivation(props.chatroomId, active)
    else store.clearActivation(props.chatroomId)
  } catch (err) {
    errorMessage.value = err instanceof ApiError ? err.message : t('activities.panel.loadFailed')
  } finally {
    loading.value = false
  }
}

async function loadTypes(projectId: string | undefined): Promise<void> {
  if (!projectId) return
  try {
    types.value = await listActivityTypes(projectId)
  } catch (err) {
    // Facilitator-only surface (the start dropdown, `v-if="isCreator"` below)
    // — a non-owner/guest 403 here must never block the participant path,
    // which never reads `types` (Q-1/AC-2).
    if (props.isCreator) {
      errorMessage.value = err instanceof ApiError ? err.message : t('activities.panel.loadFailed')
    }
  }
}

// Generation-guarded like the reconnect resync in useChatroomSocket: two
// fallback fetches can overlap when the activation changes twice in quick
// succession, and a slower earlier one must not clobber a fresher result.
let typeFetchGeneration = 0

async function ensureActiveTypeLoaded(): Promise<void> {
  const act = activation.value
  if (!act || act.activityType) {
    fetchedType.value = null
    return
  }
  if (fetchedType.value?.id === act.activityTypeId) return
  const generation = ++typeFetchGeneration
  try {
    const fetched = await getRoomActivityType(props.chatroomId, act.activityTypeId)
    if (generation !== typeFetchGeneration) return
    fetchedType.value = fetched
    errorMessage.value = null
  } catch (err) {
    if (generation !== typeFetchGeneration) return
    fetchedType.value = null
    errorMessage.value = err instanceof ApiError ? err.message : t('activities.panel.loadFailed')
  }
}

/** A translated refusal for the platform activity policy, or null.
 *
 *  The backend's `detail` already names the offending field, but as untranslated
 *  English prose and without saying who can fix it. A facilitator hits this at
 *  class time, so the message has to be actionable: which switch, and that only a
 *  Project Owner can change it ([R30.30]). The field arrives as a structured
 *  problem member so the copy can be translated rather than echoed — including
 *  the field name itself, which `refusedFieldLabel` maps to the label this UI
 *  already shows for that switch. */
function policyRefusalMessage(err: unknown): string | null {
  if (!isPolicyRefusal(err)) return null
  const field = refusedFieldLabel(err)
  return field
    ? t('activities.panel.policyRefusedField', { field })
    : t('activities.panel.policyRefused')
}

async function startForRoom(): Promise<void> {
  if (!selectedTypeId.value) return
  actionPending.value = true
  errorMessage.value = null
  try {
    store.setActivation(props.chatroomId, await startActivation(props.chatroomId, { activity_type_id: selectedTypeId.value }))
  } catch (err) {
    errorMessage.value = policyRefusalMessage(err) ?? (
      err instanceof ApiError ? err.message : t('activities.panel.startFailed')
    )
  } finally {
    actionPending.value = false
  }
}

async function endForRoom(): Promise<void> {
  if (!activation.value) return
  actionPending.value = true
  errorMessage.value = null
  try {
    await endActivation(props.chatroomId, activation.value.id)
    store.clearActivation(props.chatroomId, activation.value.id)
  } catch (err) {
    errorMessage.value = err instanceof ApiError ? err.message : t('activities.panel.endFailed')
  } finally {
    actionPending.value = false
  }
}

/** Declare finished, or undo it. Reversible on purpose: a mis-click must not
 *  cost a participant the rest of the lesson, and answering again clears the
 *  declaration server-side anyway ([R30.22]). */
async function toggleCompleted(): Promise<void> {
  const act = activation.value
  if (!act) return
  const next = !completed.value
  actionPending.value = true
  errorMessage.value = null
  try {
    const updated = await setActivationCompletion(props.chatroomId, act.id, next)
    completed.value = updated.completed_at !== null
  } catch (err) {
    errorMessage.value = err instanceof ApiError ? err.message : t('activities.panel.markDoneFailed')
  } finally {
    actionPending.value = false
  }
}

/** Seed the participant's own declaration for the round now in force.
 *
 *  The client holds no session id, so without this read the toggle would render
 *  "not done" for someone who had already declared themselves finished — every
 *  reload, every rail-tab remount. Guarded on the activation the read was issued
 *  for, so a slower read for a round that has since ended cannot clobber it. */
async function loadOwnDeclaration(): Promise<void> {
  const act = activation.value
  if (!act) return
  try {
    const own = await getOwnRoundSession(props.chatroomId, act.id)
    if (activation.value?.id !== act.id) return
    completed.value = own?.completed_at != null
  } catch {
    // The toggle stays at its default. Harmless and self-correcting: declaring
    // again is a server-side no-op whose response carries the real state, which
    // is what `toggleCompleted` writes back.
  }
}

watch(() => props.projectId, loadTypes, { immediate: true })
watch(activation, (next, previous) => {
  if (!next || (previous && next.id !== previous.id)) completed.value = false
  void ensureActiveTypeLoaded()
  void loadOwnDeclaration()
}, { immediate: true })
onMounted(() => {
  if (store.getActivation(props.chatroomId) === undefined) void hydrate()
})
</script>

<template>
  <section class="activity-panel">
    <p
      v-if="errorMessage"
      class="activity-panel__error"
      role="alert"
    >
      {{ errorMessage }}
    </p>

    <template v-if="activation">
      <p class="activity-panel__name">
        {{ activeType?.name ?? t('activities.panel.active') }}
      </p>
      <p
        v-if="isCreator && progress"
        class="activity-panel__progress"
      >
        {{ t('activities.panel.progress', { completed: progress.completed, working: progress.in_progress }) }}
      </p>
      <template v-if="activeType">
        <!-- Answering again retracts the declaration server-side ([R30.22]), so
             the toggle has to follow or it shows the wrong label and costs the
             participant two clicks to re-declare. -->
        <ActivityHost
          :chatroom-id="chatroomId"
          :activity-type="activeType"
          :session-id="null"
          :subject-user-id="session.me?.id ?? null"
          @submitted="completed = false"
        />
        <SButton
          variant="secondary"
          :loading="actionPending"
          :disabled="!session.me?.id"
          @click="toggleCompleted"
        >
          {{ completed ? t('activities.panel.markDoneUndo') : t('activities.panel.markDone') }}
        </SButton>
      </template>
      <SButton
        v-if="isCreator"
        variant="danger"
        :loading="actionPending"
        @click="endForRoom"
      >
        {{ t('activities.panel.end') }}
      </SButton>
    </template>

    <template v-else-if="isCreator">
      <SSelect
        v-model="selectedTypeId"
        :options="typeOptions"
        :placeholder="t('activities.panel.selectType')"
        :disabled="loading || !projectId"
      />
      <SButton
        variant="primary"
        :loading="actionPending"
        :disabled="!selectedTypeId"
        @click="startForRoom"
      >
        {{ t('activities.panel.startForRoom') }}
      </SButton>
    </template>

    <SEmptyState
      v-else
      :title="t('activities.panel.inactiveTitle')"
      :text="t('activities.panel.inactiveText')"
    />
  </section>
</template>

<style scoped>
.activity-panel {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  padding: 0.75rem;
  /* Owns its scroll region, like its two rail siblings (ChatroomPresence,
     ObserverPanel). A worksheet is routinely taller than the rail. */
  height: 100%;
  overflow-y: auto;
}
.activity-panel__name {
  margin: 0;
  font-weight: var(--weight-semibold);
}
.activity-panel__progress {
  margin: 0;
  font-size: var(--font-size-sm);
  color: var(--color-muted);
}
.activity-panel__error {
  margin: 0;
  color: var(--color-danger);
  font-size: var(--font-size-sm);
}
</style>
