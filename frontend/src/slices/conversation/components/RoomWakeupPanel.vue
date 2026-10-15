<script setup lang="ts">
// One bound agent's wake-up settings in this room ([R15.25]). Every value shows
// where it came from: the designer's default, this room's choice, or the agent's
// own temporary adjustment. Writes go to the room only, never to the shared
// agent, and name the binding version they were made against, so a concurrent
// change or a rebind is a reload rather than a silent overwrite.
import { computed, reactive, ref, useId, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { SBadge, SButton, SInput, SQueryError, SSelect, SSkeleton, SToggle } from '@shared/ui'
import { useConfirmDialog, useToast } from '@shared/composables'
import { ApiError } from '@shared/errors'
import { getRoomWakeup, patchRoomWakeup, resetRoomWakeup, type RoomWakeup, type RoomWakeupPatch } from '../api'
import { convKeys } from '../queries'

const props = defineProps<{
  chatroomId: string
  agentId: string
}>()

const { t } = useI18n()
const toast = useToast()
const { confirm } = useConfirmDialog()
const qc = useQueryClient()
const uid = useId()
const key = computed(() => convKeys.roomWakeup(props.chatroomId, props.agentId))

const query = useQuery({
  queryKey: key,
  queryFn: () => getRoomWakeup(props.chatroomId, props.agentId),
  retry: false,
})
const current = computed<RoomWakeup | undefined>(() => query.data.value)
// 403/404: the caller may not manage this binding (or it is gone); the panel
// simply does not render rather than showing an error for a control they lack.
const hidden = computed(() => {
  const err = query.error.value
  return err instanceof ApiError && (err.status === 403 || err.status === 404)
})

type NumberField = 'every_n_messages' | 'silence_minutes' | 'autostop_rounds'
const NUMBER_FIELDS: readonly NumberField[] = ['every_n_messages', 'silence_minutes', 'autostop_rounds']
const BOUNDS: Record<NumberField, { min: number; max: number }> = {
  every_n_messages: { min: 1, max: 1000 },
  silence_minutes: { min: 1, max: 1440 },
  autostop_rounds: { min: 1, max: 100 },
}
const drafts = reactive<Record<NumberField, number | ''>>({
  every_n_messages: '',
  silence_minutes: '',
  autostop_rounds: '',
})
// The server values each draft was last seeded from. A draft is re-seeded only
// when its own server value changes, so saving a toggle or a background refetch
// does not discard a number the teacher is still typing.
const seeded: Partial<Record<NumberField, number>> = {}
watch(
  current,
  (value) => {
    if (!value) return
    for (const field of NUMBER_FIELDS) {
      const server = value[field].value
      if (seeded[field] !== server) {
        drafts[field] = server
        seeded[field] = server
      }
    }
  },
  { immediate: true },
)

function draftNumber(raw: number | '' | string): number | null {
  // A cleared number input emits 0 or NaN, not ''; neither is a value to save.
  if (raw === '') return null
  const n = Number(raw)
  return Number.isFinite(n) && n > 0 ? n : null
}

const changedNumbers = computed<RoomWakeupPatch>(() => {
  const patch: RoomWakeupPatch = {}
  const value = current.value
  if (!value) return patch
  for (const field of NUMBER_FIELDS) {
    const draft = draftNumber(drafts[field])
    if (draft === null) continue
    const { min, max } = BOUNDS[field]
    const next = Math.min(max, Math.max(min, Math.round(draft)))
    if (next !== value[field].value) patch[field] = next
  }
  return patch
})
const hasNumberChanges = computed(() => Object.keys(changedNumbers.value).length > 0)

function adopt(next: RoomWakeup): void {
  qc.setQueryData(key.value, next)
}

async function onConflict(err: unknown): Promise<boolean> {
  if (err instanceof ApiError && err.status === 409) {
    toast.error(t('conversation.roomWakeup.conflict'))
    await qc.invalidateQueries({ queryKey: key.value })
    return true
  }
  return false
}

const saving = ref(false)

const patchMutation = useMutation({
  mutationFn: (patch: RoomWakeupPatch) => {
    const value = current.value
    if (!value) throw new Error('room wake-up settings not loaded')
    return patchRoomWakeup(props.chatroomId, props.agentId, value, patch)
  },
  onSuccess: (next) => {
    adopt(next)
    toast.success(t('conversation.roomWakeup.saved'))
  },
  onError: async (err) => {
    if (!(await onConflict(err))) toast.error(t('conversation.roomWakeup.saveFailed'))
  },
})

const resetMutation = useMutation({
  mutationFn: (scope: 'runtime' | 'room') => {
    const value = current.value
    if (!value) throw new Error('room wake-up settings not loaded')
    return resetRoomWakeup(props.chatroomId, props.agentId, value, scope)
  },
  onSuccess: (next) => {
    adopt(next)
    toast.success(t('conversation.roomWakeup.resetDone'))
  },
  onError: async (err) => {
    if (!(await onConflict(err))) toast.error(t('conversation.roomWakeup.saveFailed'))
  },
})

const busy = computed(
  () => saving.value || patchMutation.isPending.value || resetMutation.isPending.value,
)

// Writes are serialised: each names the version the previous one returned. The
// controls are disabled while one is in flight, so a second edit cannot start
// against a version that is about to change.
async function save(patch: RoomWakeupPatch): Promise<void> {
  if (busy.value || Object.keys(patch).length === 0) return
  saving.value = true
  try {
    await patchMutation.mutateAsync(patch)
  } catch {
    // Reported by the mutation's onError.
  } finally {
    saving.value = false
  }
}

async function reset(scope: 'runtime' | 'room'): Promise<void> {
  if (busy.value) return
  const ok = await confirm({
    title: t(`conversation.roomWakeup.reset.${scope}.title`),
    message: t(`conversation.roomWakeup.reset.${scope}.message`),
    confirmLabel: t('conversation.roomWakeup.reset.confirm'),
    variant: 'warning',
  })
  if (!ok) return
  saving.value = true
  try {
    await resetMutation.mutateAsync(scope)
  } catch {
    // Reported by the mutation's onError.
  } finally {
    saving.value = false
  }
}

const countOptions = computed(() => [
  { value: 'all', label: t('conversation.roomWakeup.countAll') },
  { value: 'human', label: t('conversation.roomWakeup.countHuman') },
])

const SOURCE_VARIANT = { designer: 'neutral', room: 'info', runtime: 'warning' } as const
const hasRuntime = computed(() => {
  const value = current.value
  if (!value) return false
  // Stored-but-ignored adjustments are still something the reset removes.
  if (value.ignored_agent_fields.length) return true
  return [
    value.every_n_messages_enabled,
    value.every_n_messages,
    value.silence_minutes_enabled,
    value.silence_minutes,
  ].some((f) => f.source === 'runtime')
})
const hasRoom = computed(() => {
  const value = current.value
  if (!value) return false
  return [
    value.every_n_messages_enabled,
    value.every_n_messages,
    value.count,
    value.silence_minutes_enabled,
    value.silence_minutes,
    value.autostop_rounds,
  ].some((f) => f.source === 'room')
})

function fid(name: string): string {
  return `${uid}-${name}`
}
</script>

<template>
  <section
    v-if="!hidden"
    class="room-wakeup"
    :aria-label="t('conversation.roomWakeup.title')"
  >
    <h3 class="room-wakeup__title">
      {{ t('conversation.roomWakeup.title') }}
    </h3>
    <p class="room-wakeup__desc">
      {{ t('conversation.roomWakeup.description') }}
    </p>

    <SSkeleton
      v-if="query.isPending.value"
      :lines="3"
    />
    <SQueryError
      v-else-if="query.isError.value"
      :message="t('conversation.roomWakeup.loadFailed')"
      :retry-label="t('conversation.roomWakeup.retry')"
      @retry="query.refetch()"
    />
    <template v-else-if="current">
      <p
        v-if="current.call_only"
        class="room-wakeup__note"
      >
        {{ t('conversation.roomWakeup.callOnly') }}
      </p>

      <div class="room-wakeup__row">
        <SToggle
          :id="fid('every')"
          :model-value="current.every_n_messages_enabled.value"
          size="sm"
          :disabled="busy"
          @update:model-value="(v) => save({ every_n_messages_enabled: v })"
        >
          {{ t('conversation.roomWakeup.everyNEnabled') }}
        </SToggle>
        <SBadge
          size="sm"
          :variant="SOURCE_VARIANT[current.every_n_messages_enabled.source]"
        >
          {{ t(`conversation.roomWakeup.source.${current.every_n_messages_enabled.source}`) }}
        </SBadge>
      </div>

      <div class="room-wakeup__row">
        <label
          :for="fid('n')"
          class="room-wakeup__label"
        >{{ t('conversation.roomWakeup.everyN') }}</label>
        <SInput
          :id="fid('n')"
          v-model="drafts.every_n_messages"
          type="number"
          size="sm"
          :min="BOUNDS.every_n_messages.min"
          :max="BOUNDS.every_n_messages.max"
          :disabled="busy"
          class="room-wakeup__number"
        />
        <SBadge
          size="sm"
          :variant="SOURCE_VARIANT[current.every_n_messages.source]"
        >
          {{ t(`conversation.roomWakeup.source.${current.every_n_messages.source}`) }}
        </SBadge>
        <span class="room-wakeup__inherited">
          {{ t('conversation.roomWakeup.designerValue', { value: current.every_n_messages.designer_value }) }}
        </span>
      </div>

      <div class="room-wakeup__row">
        <label
          :for="fid('count')"
          class="room-wakeup__label"
        >{{ t('conversation.roomWakeup.count') }}</label>
        <SSelect
          :id="fid('count')"
          :model-value="current.count.value"
          :options="countOptions"
          size="sm"
          :disabled="busy"
          @update:model-value="(v) => save({ count: v as 'all' | 'human' })"
        />
        <SBadge
          size="sm"
          :variant="SOURCE_VARIANT[current.count.source]"
        >
          {{ t(`conversation.roomWakeup.source.${current.count.source}`) }}
        </SBadge>
      </div>

      <div class="room-wakeup__row">
        <SToggle
          :id="fid('silence')"
          :model-value="current.silence_minutes_enabled.value"
          size="sm"
          :disabled="busy"
          @update:model-value="(v) => save({ silence_minutes_enabled: v })"
        >
          {{ t('conversation.roomWakeup.silenceEnabled') }}
        </SToggle>
        <SBadge
          size="sm"
          :variant="SOURCE_VARIANT[current.silence_minutes_enabled.source]"
        >
          {{ t(`conversation.roomWakeup.source.${current.silence_minutes_enabled.source}`) }}
        </SBadge>
      </div>

      <div class="room-wakeup__row">
        <label
          :for="fid('t')"
          class="room-wakeup__label"
        >{{ t('conversation.roomWakeup.silenceMinutes') }}</label>
        <SInput
          :id="fid('t')"
          v-model="drafts.silence_minutes"
          type="number"
          size="sm"
          :min="BOUNDS.silence_minutes.min"
          :max="BOUNDS.silence_minutes.max"
          :disabled="busy"
          class="room-wakeup__number"
        />
        <SBadge
          size="sm"
          :variant="SOURCE_VARIANT[current.silence_minutes.source]"
        >
          {{ t(`conversation.roomWakeup.source.${current.silence_minutes.source}`) }}
        </SBadge>
        <span class="room-wakeup__inherited">
          {{ t('conversation.roomWakeup.designerValue', { value: current.silence_minutes.designer_value }) }}
        </span>
      </div>

      <div class="room-wakeup__row">
        <label
          :for="fid('autostop')"
          class="room-wakeup__label"
        >{{ t('conversation.roomWakeup.autostop') }}</label>
        <SInput
          :id="fid('autostop')"
          v-model="drafts.autostop_rounds"
          type="number"
          size="sm"
          :min="BOUNDS.autostop_rounds.min"
          :max="BOUNDS.autostop_rounds.max"
          :disabled="busy"
          class="room-wakeup__number"
        />
        <SBadge
          size="sm"
          :variant="SOURCE_VARIANT[current.autostop_rounds.source]"
        >
          {{ t(`conversation.roomWakeup.source.${current.autostop_rounds.source}`) }}
        </SBadge>
        <span class="room-wakeup__inherited">
          {{ t('conversation.roomWakeup.designerValue', { value: current.autostop_rounds.designer_value }) }}
        </span>
      </div>

      <div class="room-wakeup__actions">
        <SButton
          size="sm"
          variant="primary"
          :disabled="busy || !hasNumberChanges"
          @click="save(changedNumbers)"
        >
          {{ t('conversation.roomWakeup.saveNumbers') }}
        </SButton>
        <SButton
          size="sm"
          :disabled="busy || !hasRuntime"
          @click="reset('runtime')"
        >
          {{ t('conversation.roomWakeup.reset.runtime.button') }}
        </SButton>
        <SButton
          size="sm"
          variant="danger"
          :disabled="busy || (!hasRoom && !hasRuntime)"
          @click="reset('room')"
        >
          {{ t('conversation.roomWakeup.reset.room.button') }}
        </SButton>
      </div>

      <p class="room-wakeup__note">
        {{
          current.runtime_refresh_at
            ? t('conversation.roomWakeup.refreshAt', { at: new Date(current.runtime_refresh_at).toLocaleString() })
            : t('conversation.roomWakeup.noRuntime')
        }}
      </p>
      <p
        v-if="current.ignored_agent_fields.length"
        class="room-wakeup__note"
      >
        {{ t('conversation.roomWakeup.ignored') }}
      </p>
    </template>
  </section>
</template>

<style scoped>
.room-wakeup {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding-top: var(--space-2);
}

.room-wakeup__title {
  font-weight: 600;
}

.room-wakeup__desc,
.room-wakeup__note,
.room-wakeup__inherited {
  color: var(--color-text-muted);
  font-size: var(--text-sm);
}

.room-wakeup__row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  align-items: center;
}

.room-wakeup__label {
  min-width: 10rem;
  font-size: var(--text-sm);
}

.room-wakeup__number {
  width: 6rem;
}

.room-wakeup__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}
</style>
