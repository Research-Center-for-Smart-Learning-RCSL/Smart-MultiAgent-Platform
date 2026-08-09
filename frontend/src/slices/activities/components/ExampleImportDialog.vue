<script setup lang="ts">
// The Project Owner's opt-in surface for platform-installed examples ([R30.33]).
//
// Not shared with the admin slice's install dialog: `activities` and `admin` may
// not import each other (eslint.config.js SLICE_DEPS), so the two are separate
// components by design rather than by omission. They also answer different
// questions -- "install this course platform-wide" vs "use this in my project".
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import {
  ExclamationTriangleIcon,
  PuzzlePieceIcon,
  SparklesIcon,
} from '@heroicons/vue/24/outline'

import { SModal, SButton, SBadge, SEmptyState, SQueryError, SLoadingSpinner } from '@shared/ui'
import { useConfirmDialog, useToast } from '@shared/composables'

import { listPlatformExamples, optIntoActivityType, optOutOfActivityType } from '../api'
import { activityKeys } from '../queries'

const props = defineProps<{ projectId: string; open: boolean }>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'changed'): void }>()

const { t } = useI18n()
const qc = useQueryClient()
const toast = useToast()
const { confirm } = useConfirmDialog()

/** Which row is mid-request, so only its own button shows a pending state. */
const pendingId = ref<string | null>(null)

const examplesQuery = useQuery({
  queryKey: activityKeys.examples(props.projectId),
  queryFn: () => listPlatformExamples(props.projectId),
  // Only while the dialog is open: this is a Project Owner-gated call and there
  // is no reason to make it on every visit to the types page.
  enabled: computed(() => props.open),
})

const examples = computed(() => examplesQuery.data.value ?? [])
const isLoading = computed(() => examplesQuery.isLoading.value)
const isError = computed(() => examplesQuery.isError.value)

/** Any example that sends participant text to the project's LLM provider. The
 *  banner is shown whenever one exists, not only after a row is toggled: the
 *  owner needs it before choosing, not as a consequence of having chosen. */
const anyExposesToAgent = computed(() => examples.value.some((e) => e.expose_payload_to_agent))

function invalidate(): void {
  void qc.invalidateQueries({ queryKey: activityKeys.examples(props.projectId) })
  // The types table gains or loses a row, so it has to refetch too.
  void qc.invalidateQueries({ queryKey: activityKeys.types(props.projectId) })
  emit('changed')
}

const enableMutation = useMutation({
  mutationFn: (typeId: string) => optIntoActivityType(props.projectId, typeId),
  onSuccess: () => {
    invalidate()
    toast.success(t('activities.examples.enabled'))
  },
  onError: () => {
    invalidate()
    toast.error(t('activities.examples.enableFailed'))
  },
  onSettled: () => {
    pendingId.value = null
  },
})

const disableMutation = useMutation({
  mutationFn: (typeId: string) => optOutOfActivityType(props.projectId, typeId),
  onSuccess: () => {
    invalidate()
    toast.success(t('activities.examples.disabled'))
  },
  onError: () => {
    invalidate()
    toast.error(t('activities.examples.disableFailed'))
  },
  onSettled: () => {
    pendingId.value = null
  },
})

function enable(typeId: string): void {
  pendingId.value = typeId
  enableMutation.mutate(typeId)
}

async function disable(typeId: string, name: string): Promise<void> {
  // Disabling ends any running activation of this type in this project's rooms
  // and closes its open sessions, so it is destructive in a way enabling is not.
  const ok = await confirm({
    title: t('activities.examples.disableTitle'),
    message: t('activities.examples.disableConfirm', { name }),
    variant: 'warning',
  })
  if (!ok) return
  pendingId.value = typeId
  disableMutation.mutate(typeId)
}
</script>

<template>
  <SModal
    :open="open"
    :title="t('activities.examples.title')"
    size="lg"
    @close="emit('close')"
  >
    <div class="flex flex-col gap-4">
      <p class="text-sm text-[var(--color-muted)]">
        {{ t('activities.examples.intro') }}
      </p>

      <SQueryError
        v-if="isError"
        :message="t('activities.examples.errorText')"
        :retry-label="t('activities.typesList.retry')"
        @retry="examplesQuery.refetch()"
      />

      <div
        v-else-if="isLoading"
        class="flex justify-center py-8"
      >
        <SLoadingSpinner />
      </div>

      <SEmptyState
        v-else-if="examples.length === 0"
        :icon="PuzzlePieceIcon"
        :title="t('activities.examples.emptyTitle')"
        :text="t('activities.examples.emptyText')"
      />

      <template v-else>
        <div
          v-if="anyExposesToAgent"
          class="flex gap-2 rounded-md border border-[var(--color-warning)] p-3"
          role="note"
        >
          <ExclamationTriangleIcon class="w-5 h-5 shrink-0 text-[var(--color-warning)]" />
          <p class="text-sm text-[var(--color-fg)]">
            {{ t('activities.examples.agentExposureNotice') }}
          </p>
        </div>

        <ul class="flex flex-col gap-3">
          <li
            v-for="example in examples"
            :key="example.id"
            class="flex items-start justify-between gap-4 rounded-md border border-[var(--color-border)] p-3"
          >
            <div class="flex flex-col gap-1">
              <div class="flex items-center gap-2">
                <SparklesIcon class="w-4 h-4 text-[var(--color-muted)]" />
                <span class="font-medium">{{ example.name }}</span>
                <SBadge
                  v-if="example.enabled"
                  size="sm"
                  variant="success"
                >
                  {{ t('activities.examples.enabledBadge') }}
                </SBadge>
              </div>
              <span class="font-mono text-xs text-[var(--color-muted)]">{{ example.key }}</span>
              <div class="flex flex-wrap gap-2 pt-1">
                <SBadge
                  v-if="example.expose_payload_to_agent"
                  size="sm"
                  variant="warning"
                >
                  {{ t('activities.examples.exposesToAgent') }}
                </SBadge>
                <SBadge
                  v-if="example.echo_includes_content"
                  size="sm"
                  variant="warning"
                >
                  {{ t('activities.examples.echoesContent') }}
                </SBadge>
                <SBadge
                  v-if="example.retention_days !== null"
                  size="sm"
                  variant="neutral"
                >
                  {{ t('activities.examples.retention', { days: example.retention_days }) }}
                </SBadge>
              </div>
            </div>

            <SButton
              v-if="example.enabled"
              variant="secondary"
              size="sm"
              :disabled="pendingId === example.id"
              @click="disable(example.id, example.name)"
            >
              {{ t('activities.examples.disable') }}
            </SButton>
            <SButton
              v-else
              variant="primary"
              size="sm"
              :disabled="pendingId === example.id"
              @click="enable(example.id)"
            >
              {{ t('activities.examples.enable') }}
            </SButton>
          </li>
        </ul>
      </template>
    </div>

    <template #footer>
      <SButton
        variant="secondary"
        @click="emit('close')"
      >
        {{ t('common.close', 'Close') }}
      </SButton>
    </template>
  </SModal>
</template>
