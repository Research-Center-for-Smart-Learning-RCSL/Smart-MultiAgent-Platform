<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ApiError } from '@shared/errors'
import { SButton, SEmptyState, SSelect } from '@shared/ui'
import { useSessionStore } from '@shared/stores/session'
import { closeActivitySession, endActivation, getActiveActivation, listActivityTypes, openActivitySession, startActivation } from '../api'
import { useActivitiesStore } from '../stores/activities'
import type { ActivitySession, ActivityType } from '../types'
import ActivityHost from './ActivityHost.vue'

const props = defineProps<{
  chatroomId: string
  projectId?: string | undefined
  isCreator: boolean
}>()

const { t } = useI18n()
const session = useSessionStore()
const store = useActivitiesStore()
const types = ref<ActivityType[]>([])
const selectedTypeId = ref<string | null>(null)
const activitySession = ref<ActivitySession | null>(null)
const loading = ref(false)
const actionPending = ref(false)
const errorMessage = ref<string | null>(null)

const activation = computed(() => store.getActivation(props.chatroomId) ?? null)
const activeType = computed(() =>
  types.value.find((activityType) => activityType.id === activation.value?.activityTypeId) ?? null,
)
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
    errorMessage.value = err instanceof ApiError ? err.message : t('activities.panel.loadFailed')
  }
}

async function startForRoom(): Promise<void> {
  if (!selectedTypeId.value) return
  actionPending.value = true
  errorMessage.value = null
  try {
    store.setActivation(props.chatroomId, await startActivation(props.chatroomId, { activity_type_id: selectedTypeId.value }))
  } catch (err) {
    errorMessage.value = err instanceof ApiError ? err.message : t('activities.panel.startFailed')
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
})
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
