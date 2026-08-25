<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ApiError } from '@shared/errors'
import { SButton, SEmptyState, SLoadingSpinner, SSelect } from '@shared/ui'
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
import { useGroupProposal } from '../composables/useGroupProposal'
import { usePolicyRefusal } from '../composables/usePolicyRefusal'
import { useActivitiesStore } from '../stores/activities'
import { isProposalOpen, readGroupConsent, type ActivityType, type ActivityTypePublic } from '../types'
import ActivityHost from './ActivityHost.vue'
import GroupProposalCard from './GroupProposalCard.vue'
import SchemaForm from './SchemaForm.vue'

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

// ---- group mode ([R30.40], [R30.41]) ---------------------------------------
// Two conditions, both server-sourced and both necessary: the TYPE declares a
// consent fraction, and the SERVER says this caller belongs to a group bound to
// this room. Either alone would show a picker that cannot produce a submission
// — a group task to a student in no group, or a group vote on a personal one.

const consent = computed(() => readGroupConsent(activeType.value?.group_config))
const group = useGroupProposal({
  chatroomId: () => props.chatroomId,
  activationId: () => (consent.value ? (activation.value?.id ?? null) : null),
  activityTypeId: () => activation.value?.activityTypeId ?? null,
  viewerUserId: () => session.me?.id ?? null,
  isCreator: () => props.isCreator,
})
// Three states, not two. Until the server has answered for this round, whether
// this caller submits with a group is UNKNOWN — and showing the individual
// worksheet meanwhile invites them to type an answer into a surface that is
// about to be replaced. `groupModePending` is that gap; the read failing is
// shown as a failure rather than silently becoming "no group".
const isGroupMode = computed(
  () => !!consent.value && group.roundResolved.value && group.canPropose.value,
)
const groupModePending = computed(() => !!consent.value && !group.roundResolved.value)
const groupOptions = computed(() =>
  group.eligibleGroups.value.map((g) => ({ value: g.id, label: g.name })),
)
const selectedGroupId = ref<string | null>(null)

// A caller in exactly one group is the ordinary classroom shape, and asking
// them to pick from a list of one is a click that carries no decision.
watch(
  groupOptions,
  (options) => {
    if (options.length === 1) selectedGroupId.value = options[0]!.value
    else if (!options.some((o) => o.value === selectedGroupId.value)) selectedGroupId.value = null
  },
  { immediate: true },
)

function onProposalSubmit(payload: Record<string, unknown>): void {
  if (!selectedGroupId.value) return
  void group.propose(selectedGroupId.value, payload)
}

// A settled proposal that did NOT accept leaves the group free to try again;
// an accepted one is this round's answer and the form stays away.
const canProposeAgain = computed(() => {
  const shown = group.visibleProposal.value
  return !!shown && !isProposalOpen(shown.status) && shown.status !== 'accepted'
})

// Re-read the open proposal at call time rather than closing over the render's
// value: a broadcast can resolve it between paint and click, and a non-null
// assertion there would throw where nothing catches.
function onVote(approve: boolean): void {
  const open = group.openProposal.value
  if (open) void group.vote(open.id, approve)
}

function onWithdraw(): void {
  const open = group.openProposal.value
  if (open) void group.withdraw(open.id)
}
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
      <!-- Named to everyone, not just the facilitator ([R30.37]): an agent bound
           to a room is already named on every message it sends, so this discloses
           nothing new — and a round nobody can attribute is worse. -->
      <p
        v-if="activation.startedByAgentName"
        class="activity-panel__initiator"
      >
        {{ t('activities.panel.startedByAgent', { agent: activation.startedByAgentName }) }}
      </p>
      <p class="activity-panel__name">
        {{ activeType?.name ?? t('activities.panel.active') }}
      </p>
      <p
        v-if="isCreator && progress"
        class="activity-panel__progress"
      >
        {{ t('activities.panel.progress', { completed: progress.completed, working: progress.in_progress }) }}
      </p>
      <!-- The round's group state has not come back yet. Neither worksheet is
           the right one to show, so show neither. -->
      <template v-if="activeType && groupModePending">
        <p
          v-if="group.errorMessage.value"
          class="activity-panel__error"
          role="alert"
        >
          {{ group.errorMessage.value }}
        </p>
        <SButton
          v-if="group.errorMessage.value"
          variant="secondary"
          :loading="group.loading.value"
          data-testid="group-retry"
          @click="group.refresh()"
        >
          {{ t('activities.group.retry') }}
        </SButton>
        <SLoadingSpinner v-else />
      </template>

      <!-- Group mode: the answer is the GROUP's, so there is no personal
           "I am finished" toggle here — the submission belongs to a subject
           this participant is only part of ([R30.39]). -->
      <template v-else-if="activeType && isGroupMode">
        <p class="activity-panel__group-note">
          {{ t('activities.group.intro') }}
        </p>
        <p
          v-if="group.errorMessage.value"
          class="activity-panel__error"
          role="alert"
        >
          {{ group.errorMessage.value }}
        </p>
        <!-- Shown through the settled state too, not just while open: the
             student who cast the deciding vote has to be told their group's
             answer went in, and a blank form returning in its place would invite
             a second proposal the database does not stop. -->
        <GroupProposalCard
          v-if="group.visibleProposal.value"
          :proposal="group.visibleProposal.value"
          :group-name="group.groupName(group.visibleProposal.value.member_group_id)"
          :my-vote="group.myVote.value"
          :is-proposer="group.isProposer.value"
          :pending="group.pending.value"
          :can-vote="!!session.me?.id"
          :can-retry="canProposeAgain"
          @vote="(approve) => onVote(approve)"
          @withdraw="onWithdraw"
          @retry="group.dismissSettled()"
        />
        <template v-else>
          <SSelect
            v-if="groupOptions.length > 1"
            v-model="selectedGroupId"
            :options="groupOptions"
            :placeholder="t('activities.group.selectGroup')"
            :disabled="group.pending.value"
          />
          <SchemaForm
            :schema="activeType.payload_schema"
            :submitting="group.pending.value"
            :disabled="!selectedGroupId"
            :submit-label="t('activities.group.propose')"
            @submit="onProposalSubmit"
          />
        </template>
      </template>

      <template v-else-if="activeType">
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
  gap: var(--space-3);
  padding: var(--space-3);
  /* Owns its scroll region, like its two rail siblings (ChatroomPresence,
     ObserverPanel). A worksheet is routinely taller than the rail. */
  height: 100%;
  overflow-y: auto;
}
.activity-panel__name {
  margin: 0;
  font-weight: var(--weight-semibold);
}
.activity-panel__initiator {
  margin: 0;
  font-size: var(--font-size-sm);
  color: var(--color-muted);
}
.activity-panel__progress {
  margin: 0;
  font-size: var(--font-size-sm);
  color: var(--color-muted);
}
.activity-panel__group-note {
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
