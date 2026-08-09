<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ApiError } from '@shared/errors'
import { SButton, SEmptyState, SSelect } from '@shared/ui'
import { useSessionStore } from '@shared/stores/session'
import {
  closeActivitySession,
  endActivation,
  getActiveActivation,
  getRoomActivityType,
  listActivityTypes,
  openActivitySession,
  startActivation,
} from '../api'
import { usePolicyRefusal } from '../composables/usePolicyRefusal'
import { useActivitiesStore } from '../stores/activities'
import type { ActivitySession, ActivityType, ActivityTypePublic } from '../types'
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
const activitySession = ref<ActivitySession | null>(null)
const loading = ref(false)
const actionPending = ref(false)
const errorMessage = ref<string | null>(null)
// Fallback fetch when the activation carries no embedded type — a missed
// broadcast or a store reset (Q-1/AC-6). Keyed so a stale fetch for a since-
// ended activation is never rendered.
const fetchedType = ref<ActivityTypePublic | null>(null)

const activation = computed(() => store.getActivation(props.chatroomId) ?? null)
const activeType = computed(() => activation.value?.activityType ?? fetchedType.value)
const typeOptions = computed(() => types.value.map((activityType) => ({ value: activityType.id, label: activityType.name })))

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

async function startSession(): Promise<void> {
  if (!activeType.value || !session.me?.id) return
  actionPending.value = true
  errorMessage.value = null
  try {
    activitySession.value = await openActivitySession(props.chatroomId, {
      activity_type_id: activeType.value.id,
      subject_user_id: session.me.id,
    })
  } catch (err) {
    errorMessage.value = err instanceof ApiError ? err.message : t('activities.panel.startFailed')
  } finally {
    actionPending.value = false
  }
}

async function finishSession(): Promise<void> {
  if (!activitySession.value) return
  actionPending.value = true
  errorMessage.value = null
  try {
    await closeActivitySession(props.chatroomId, activitySession.value.id)
    activitySession.value = null
  } catch (err) {
    errorMessage.value = err instanceof ApiError ? err.message : t('activities.panel.finishFailed')
  } finally {
    actionPending.value = false
  }
}

watch(() => props.projectId, loadTypes, { immediate: true })
watch(activation, (next, previous) => {
  if (previous && next?.id !== previous.id) activitySession.value = null
  if (!next) activitySession.value = null
  void ensureActiveTypeLoaded()
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
      <template v-if="activeType">
        <template v-if="activitySession">
          <ActivityHost
            :chatroom-id="chatroomId"
            :activity-type="activeType"
            :session-id="activitySession.id"
            :subject-user-id="session.me?.id ?? null"
          />
          <SButton
            variant="secondary"
            :loading="actionPending"
            @click="finishSession"
          >
            {{ t('activities.panel.finish') }}
          </SButton>
        </template>
        <SButton
          v-else
          variant="primary"
          :loading="actionPending"
          :disabled="!session.me?.id"
          @click="startSession"
        >
          {{ t('activities.panel.join') }}
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
.activity-panel__error {
  margin: 0;
  color: var(--color-danger);
  font-size: var(--font-size-sm);
}
</style>
