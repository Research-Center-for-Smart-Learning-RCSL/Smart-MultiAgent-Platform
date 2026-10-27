<script setup lang="ts">
// When the room's teacher agents open the class on their own: once enough
// students are present, or once the first arrival has waited long enough. Room
// creator only; the parent gates the render and the server refuses everyone else.
import { computed, ref, useId, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { SButton, SCard, SInput, SQueryError, SSkeleton, SToggle } from '@shared/ui'
import { useConfirmDialog, useToast } from '@shared/composables'
import { ApiError } from '@shared/errors'
import { getClassStart, patchClassStart, type ClassStart, type ClassStartPatch } from '../api'
import { convKeys } from '../queries'

const props = defineProps<{
  chatroomId: string
}>()

const { t } = useI18n()
const toast = useToast()
const { confirm } = useConfirmDialog()
const qc = useQueryClient()
const uid = useId()
const key = computed(() => convKeys.classStart(props.chatroomId))

const MIN_PARTICIPANTS = { min: 1, max: 100 } as const
const MAX_WAIT = { min: 1, max: 60 } as const
// Offered for a room that has never been configured, since the first write
// must carry every setting.
const DEFAULT_MIN_PARTICIPANTS = 3
const DEFAULT_MAX_WAIT = 5

const query = useQuery({
  queryKey: key,
  queryFn: () => getClassStart(props.chatroomId),
  retry: false,
})
const current = computed<ClassStart | undefined>(() => query.data.value)
const hidden = computed(() => {
  const err = query.error.value
  return err instanceof ApiError && (err.status === 403 || err.status === 404)
})

const enabled = ref(false)
const minParticipants = ref<number | ''>(DEFAULT_MIN_PARTICIPANTS)
const maxWait = ref<number | ''>(DEFAULT_MAX_WAIT)

function seed(value: ClassStart): void {
  enabled.value = value.enabled
  minParticipants.value = value.min_participants ?? DEFAULT_MIN_PARTICIPANTS
  maxWait.value = value.max_wait_minutes ?? DEFAULT_MAX_WAIT
}

// Keyed on the stored settings only: a reset changes the start timestamps but
// not the settings, and must not discard what the teacher is typing.
watch(
  () => {
    const v = current.value
    return v ? `${v.configured}|${v.enabled}|${v.min_participants}|${v.max_wait_minutes}` : ''
  },
  () => {
    if (current.value) seed(current.value)
  },
  { immediate: true },
)

function inRange(raw: number | '' | string, bounds: { min: number; max: number }): number | null {
  if (raw === '') return null
  const n = Number(raw)
  return Number.isInteger(n) && n >= bounds.min && n <= bounds.max ? n : null
}

const minValue = computed(() => inRange(minParticipants.value, MIN_PARTICIPANTS))
const waitValue = computed(() => inRange(maxWait.value, MAX_WAIT))
const valid = computed(() => minValue.value !== null && waitValue.value !== null)

const dirty = computed(() => {
  const v = current.value
  if (!v) return false
  if (!v.configured) return true
  return (
    enabled.value !== v.enabled ||
    minValue.value !== v.min_participants ||
    waitValue.value !== v.max_wait_minutes
  )
})

type ClassState =
  | { kind: 'unconfigured' }
  | { kind: 'off' }
  | { kind: 'idle' }
  | { kind: 'waiting'; since: string }
  | { kind: 'started'; at: string }

const state = computed<ClassState | null>(() => {
  const v = current.value
  if (!v) return null
  if (v.started_at) return { kind: 'started', at: v.started_at }
  if (!v.configured) return { kind: 'unconfigured' }
  if (!v.enabled) return { kind: 'off' }
  if (v.first_arrival_at) return { kind: 'waiting', since: v.first_arrival_at }
  return { kind: 'idle' }
})

const stateText = computed(() => {
  const s = state.value
  if (!s) return ''
  if (s.kind === 'started') return t('conversation.classStart.state.started', { at: formatTime(s.at) })
  if (s.kind === 'waiting') return t('conversation.classStart.state.waiting', { since: formatTime(s.since) })
  return t(`conversation.classStart.state.${s.kind}`)
})

const canReset = computed(() => !!current.value?.started_at || !!current.value?.first_arrival_at)

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString()
}

const mutation = useMutation({
  mutationFn: (patch: ClassStartPatch) => patchClassStart(props.chatroomId, patch),
  onSuccess: (next) => {
    qc.setQueryData(key.value, next)
  },
})
const busy = computed(() => mutation.isPending.value)

async function save(): Promise<void> {
  if (busy.value || !valid.value || !dirty.value) return
  try {
    await mutation.mutateAsync({
      enabled: enabled.value,
      min_participants: minValue.value,
      max_wait_minutes: waitValue.value,
    })
    toast.success(t('conversation.classStart.saved'))
  } catch {
    toast.error(t('conversation.classStart.saveFailed'))
  }
}

async function reset(): Promise<void> {
  if (busy.value || !canReset.value) return
  const ok = await confirm({
    title: t('conversation.classStart.resetTitle'),
    message: t('conversation.classStart.resetMessage'),
    confirmLabel: t('conversation.classStart.reset'),
    variant: 'warning',
  })
  if (!ok) return
  try {
    await mutation.mutateAsync({ reset: true })
    toast.success(t('conversation.classStart.resetDone'))
  } catch {
    toast.error(t('conversation.classStart.saveFailed'))
  }
}

function fid(name: string): string {
  return `${uid}-${name}`
}
</script>

<template>
  <SCard
    v-if="!hidden"
    data-testid="class-start-card"
  >
    <h2 class="class-start__heading">
      {{ t('conversation.classStart.title') }}
    </h2>
    <p class="class-start__desc">
      {{ t('conversation.classStart.description') }}
    </p>

    <SSkeleton
      v-if="query.isPending.value"
      :lines="3"
    />
    <SQueryError
      v-else-if="query.isError.value"
      :message="t('conversation.classStart.loadFailed')"
      :retry-label="t('conversation.classStart.retry')"
      @retry="query.refetch()"
    />
    <template v-else-if="current">
      <p
        class="class-start__state"
        data-testid="class-start-state"
      >
        {{ stateText }}
      </p>

      <SToggle
        v-model="enabled"
        size="sm"
        :disabled="busy"
        data-testid="class-start-enabled"
      >
        {{ t('conversation.classStart.enabled') }}
      </SToggle>

      <div class="class-start__row">
        <label
          :for="fid('min')"
          class="class-start__label"
        >{{ t('conversation.classStart.minParticipants') }}</label>
        <SInput
          :id="fid('min')"
          v-model="minParticipants"
          type="number"
          size="sm"
          :min="MIN_PARTICIPANTS.min"
          :max="MIN_PARTICIPANTS.max"
          :error="minValue === null"
          :disabled="busy"
          class="class-start__number"
          data-testid="class-start-min"
        />
      </div>

      <div class="class-start__row">
        <label
          :for="fid('wait')"
          class="class-start__label"
        >{{ t('conversation.classStart.maxWait') }}</label>
        <SInput
          :id="fid('wait')"
          v-model="maxWait"
          type="number"
          size="sm"
          :min="MAX_WAIT.min"
          :max="MAX_WAIT.max"
          :error="waitValue === null"
          :disabled="busy"
          class="class-start__number"
          data-testid="class-start-wait"
        />
      </div>

      <p
        v-if="!valid"
        class="class-start__error"
        role="alert"
      >
        {{ t('conversation.classStart.invalid') }}
      </p>

      <div class="class-start__actions">
        <SButton
          size="sm"
          variant="primary"
          :loading="busy"
          :disabled="busy || !valid || !dirty"
          data-testid="class-start-save"
          @click="save"
        >
          {{ t('conversation.classStart.save') }}
        </SButton>
        <SButton
          size="sm"
          :disabled="busy || !canReset"
          data-testid="class-start-reset"
          @click="reset"
        >
          {{ t('conversation.classStart.reset') }}
        </SButton>
      </div>
    </template>
  </SCard>
</template>

<style scoped>
.class-start__heading {
  font-size: var(--font-size-lg);
  font-weight: var(--weight-semibold);
  color: var(--color-fg);
  margin-bottom: var(--space-2);
}

.class-start__desc,
.class-start__state {
  font-size: var(--font-size-sm);
  color: var(--color-muted);
  margin-bottom: var(--space-3);
}

.class-start__state {
  color: var(--color-fg);
}

.class-start__row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-3);
}

.class-start__label {
  min-width: 12rem;
  font-size: var(--font-size-sm);
}

.class-start__number {
  width: 6rem;
}

.class-start__error {
  margin-top: var(--space-2);
  font-size: var(--font-size-xs);
  color: var(--color-danger);
}

.class-start__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-top: var(--space-4);
}
</style>
