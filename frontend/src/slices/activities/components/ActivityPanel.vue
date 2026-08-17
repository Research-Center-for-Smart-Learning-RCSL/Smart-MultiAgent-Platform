<script setup lang="ts">
import { computed, onMounted, onScopeDispose, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ApiError } from '@shared/errors'
import { SButton, SEmptyState, SSelect } from '@shared/ui'
import { wsManager, type ChannelEvent } from '@shared/transport'
import { useSessionStore } from '@shared/stores/session'
import {
  endActivation,
  getActivationProgress,
  getActiveActivation,
  getOwnRoundSession,
  getRoomActivityType,
  listActivityTypes,
  setActivationCompletion,
  startActivation,
} from '../api'
import { usePolicyRefusal } from '../composables/usePolicyRefusal'
import { useActivitiesStore } from '../stores/activities'
import type {
  ActivityActivationProgress,
  ActivityType,
  ActivityTypePublic,
} from '../types'
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
// The participant's own "I am finished" declaration for the current round, and
// the facilitator's view of the class. Both are per-activation, so both reset
// in the activation watcher below.
const completed = ref(false)
const progress = ref<ActivityActivationProgress | null>(null)

const activation = computed(() => store.getActivation(props.chatroomId) ?? null)
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

// Generation-guarded for the same reason as the type fetch: the activation can
// change twice before a slower read for the older round resolves.
let roundReadGeneration = 0

/** Seed both per-round reads for the activation now in force.
 *
 *  The completion read is what a reloading participant needs — the client holds
 *  no session id, so without it the toggle would render "not done" for someone
 *  who already declared themselves finished. The progress read is the
 *  facilitator's and 403s for everyone else, which is the expected answer rather
 *  than a failure, so it never touches `errorMessage`. */
async function loadRoundState(): Promise<void> {
  const act = activation.value
  if (!act) return
  const generation = ++roundReadGeneration
  try {
    const own = await getOwnRoundSession(props.chatroomId, act.id)
    if (generation !== roundReadGeneration) return
    completed.value = own?.completed_at != null
  } catch {
    // A failed read leaves the toggle at its default; the participant can still
    // declare, and the server is the authority on what that does.
  }
  if (props.isCreator) await refreshProgress(generation)
}

async function refreshProgress(generation = roundReadGeneration): Promise<void> {
  const act = activation.value
  if (!act || !props.isCreator) return
  try {
    const next = await getActivationProgress(props.chatroomId, act.id)
    if (generation !== roundReadGeneration) return
    progress.value = next
  } catch {
    // Room-creator-gated: a 403 is the expected answer for an admin viewing a
    // room they do not own, and a transient failure must not blank a count that
    // was correct a moment ago.
  }
}

// ---- live progress over /ws/user/{id} ---------------------------------------
// The completion event is addressed to the facilitator who started the round
// ([R30.22]), never the room channel — the counts would otherwise tell every
// participant how many peers had finished. A viewer who passes the room-creator
// gate without being the starter (an admin, a moderator on a legacy
// NULL-creator room) receives nothing, so they poll, exactly as the observer
// panel does for the same reason.
const POLL_MS = 30_000
const unsubs: Array<() => void> = []
let pollTimer: ReturnType<typeof setInterval> | null = null

function teardownLive(): void {
  for (const u of unsubs.splice(0)) u()
  if (pollTimer !== null) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

watch(
  () => [session.me?.id, props.isCreator, activation.value?.id, activation.value?.startedByUserId],
  ([userId, isCreator, activationId, startedBy]) => {
    teardownLive()
    if (!userId || !isCreator || !activationId) return
    if (startedBy === userId) {
      const channel = wsManager.channel(`/user/${userId}`)
      unsubs.push(
        channel.subscribe('activity.session.completion', (ev: ChannelEvent) => {
          if (ev.chatroom_id !== props.chatroomId || ev.activation_id !== activationId) return
          progress.value = {
            completed: Number(ev.completed ?? 0),
            in_progress: Number(ev.in_progress ?? 0),
          }
        }),
      )
      // Idempotent — ban-kick / notifications likely connected it already.
      channel.connect()
    } else {
      pollTimer = setInterval(() => void refreshProgress(), POLL_MS)
    }
  },
  { immediate: true },
)

onScopeDispose(teardownLive)

watch(() => props.projectId, loadTypes, { immediate: true })
watch(activation, (next, previous) => {
  if (!next || (previous && next.id !== previous.id)) {
    completed.value = false
    progress.value = null
  }
  void ensureActiveTypeLoaded()
  void loadRoundState()
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
        <ActivityHost
          :chatroom-id="chatroomId"
          :activity-type="activeType"
          :session-id="null"
          :subject-user-id="session.me?.id ?? null"
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
