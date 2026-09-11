<script setup lang="ts">
import { ref, computed, toRef } from 'vue'
import { useI18n } from 'vue-i18n'
import { useQuery, useQueryClient, useMutation } from '@tanstack/vue-query'
import { XMarkIcon, ArrowPathIcon } from '@heroicons/vue/24/outline'
import { useConfirmDialog } from '@shared/composables/useConfirmDialog'
import { useToast } from '@shared/composables/useToast'
import SRelativeTime from '@shared/ui/SRelativeTime.vue'
import SLoadingSpinner from '@shared/ui/SLoadingSpinner.vue'
import { canvasKeys } from '../queries'
import * as canvasApi from '../api'
import type { CanvasSnapshot, CanvasSnapshotDetail } from '../types'

const { t } = useI18n()
const { confirm } = useConfirmDialog()
const toast = useToast()
const queryClient = useQueryClient()

const props = defineProps<{
  chatroomId: string
}>()

const emit = defineEmits<{
  close: []
}>()

const chatroomIdRef = toRef(props, 'chatroomId')

const snapshotsQuery = useQuery({
  queryKey: computed(() => canvasKeys.snapshots(chatroomIdRef.value)),
  queryFn: () => canvasApi.listSnapshots(chatroomIdRef.value, { limit: 50 }),
  enabled: computed(() => !!chatroomIdRef.value),
})

const snapshots = computed<CanvasSnapshot[]>(() => snapshotsQuery.data.value ?? [])

const previewSnapshotId = ref<string | null>(null)

const previewQuery = useQuery({
  queryKey: computed(() =>
    previewSnapshotId.value
      ? canvasKeys.snapshotDetail(chatroomIdRef.value, previewSnapshotId.value)
      : ['__disabled__'],
  ),
  queryFn: () => canvasApi.getSnapshot(chatroomIdRef.value, previewSnapshotId.value!),
  enabled: computed(() => !!previewSnapshotId.value),
})

const previewData = computed<CanvasSnapshotDetail | undefined>(() => previewQuery.data.value)

function selectSnapshot(id: string) {
  previewSnapshotId.value = previewSnapshotId.value === id ? null : id
}

const restoreMut = useMutation({
  mutationFn: (snapshotId: string) => canvasApi.restoreSnapshot(chatroomIdRef.value, snapshotId),
  onSuccess: () => {
    queryClient.invalidateQueries({ queryKey: canvasKeys.snapshots(chatroomIdRef.value) })
    queryClient.invalidateQueries({ queryKey: canvasKeys.objects(chatroomIdRef.value) })
    toast.success(t('canvas.restored'))
    previewSnapshotId.value = null
  },
})

async function handleRestore(snapshotId: string) {
  const ok = await confirm({
    title: t('canvas.restore'),
    message: t('canvas.restoreConfirm'),
    confirmText: t('canvas.restore'),
    variant: 'warning',
  })
  if (!ok) return
  await restoreMut.mutateAsync(snapshotId)
}
</script>

<template>
  <div class="canvas-history">
    <div class="canvas-history__header">
      <span class="canvas-history__title">{{ t('canvas.historyTitle') }}</span>
      <button
        class="canvas-history__close"
        :title="t('canvas.close')"
        @click="emit('close')"
      >
        <XMarkIcon class="canvas-history__close-icon" />
      </button>
    </div>

    <div class="canvas-history__body">
      <div
        v-if="snapshotsQuery.isLoading.value"
        class="canvas-history__centered"
      >
        <SLoadingSpinner />
      </div>

      <div
        v-else-if="snapshots.length === 0"
        class="canvas-history__empty"
      >
        {{ t('canvas.noSnapshots') }}
      </div>

      <div
        v-else
        class="canvas-history__list"
      >
        <div
          v-for="snap in snapshots"
          :key="snap.id"
          class="canvas-history__item"
          :class="{ 'canvas-history__item--selected': previewSnapshotId === snap.id }"
          @click="selectSnapshot(snap.id)"
        >
          <div class="canvas-history__item-header">
            <SRelativeTime
              :value="snap.created_at"
              class="canvas-history__time"
            />
          </div>
          <div
            v-if="snap.label"
            class="canvas-history__label"
          >
            {{ snap.label }}
          </div>
          <div
            v-if="snap.agent_digest"
            class="canvas-history__digest"
          >
            {{ snap.agent_digest }}
          </div>

          <div
            v-if="previewSnapshotId === snap.id"
            class="canvas-history__actions"
          >
            <button
              class="canvas-history__restore-btn"
              :disabled="restoreMut.isPending.value"
              @click.stop="handleRestore(snap.id)"
            >
              <ArrowPathIcon class="canvas-history__restore-icon" />
              {{ restoreMut.isPending.value ? t('canvas.restoring') : t('canvas.restore') }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <div
      v-if="previewData"
      class="canvas-history__preview"
    >
      <div class="canvas-history__preview-label">
        {{ t('canvas.preview') }}
      </div>
      <div class="canvas-history__preview-info">
        {{ previewData.agent_digest }}
      </div>
    </div>
  </div>
</template>

<style scoped>
.canvas-history {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: var(--color-surface);
  border-left: 1px solid var(--color-border);
  width: 320px;
  max-width: 100%;
}

.canvas-history__header {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid var(--color-border);
}

.canvas-history__title {
  font-weight: var(--weight-semibold);
  font-size: var(--font-size-sm);
}

.canvas-history__close {
  margin-left: auto;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  background: transparent;
  color: var(--color-muted);
  cursor: pointer;
  border-radius: var(--radius-sm);
}

.canvas-history__close:hover {
  background: var(--color-surface-hover);
  color: var(--color-fg);
}

.canvas-history__close-icon {
  width: 18px;
  height: 18px;
}

.canvas-history__body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.canvas-history__centered {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-8);
}

.canvas-history__empty {
  padding: var(--space-8) var(--space-4);
  text-align: center;
  color: var(--color-muted);
  font-size: var(--font-size-sm);
}

.canvas-history__list {
  display: flex;
  flex-direction: column;
}

.canvas-history__item {
  padding: var(--space-3);
  border-bottom: 1px solid var(--color-border);
  cursor: pointer;
  transition: background 0.15s;
}

.canvas-history__item:hover {
  background: var(--color-surface-hover);
}

.canvas-history__item--selected {
  background: var(--color-surface-active, var(--color-surface-hover));
  border-left: 3px solid var(--color-primary);
}

.canvas-history__item-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.canvas-history__time {
  font-size: var(--font-size-xs);
  color: var(--color-muted);
}

.canvas-history__label {
  margin-top: var(--space-1);
  font-size: var(--font-size-sm);
  font-weight: var(--weight-medium);
  color: var(--color-fg);
}

.canvas-history__digest {
  margin-top: var(--space-0-5);
  font-size: var(--font-size-xs);
  color: var(--color-muted);
}

.canvas-history__actions {
  margin-top: var(--space-2);
}

.canvas-history__restore-btn {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: var(--space-1) var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  color: var(--color-fg);
  font-size: var(--font-size-xs);
  cursor: pointer;
  transition: background 0.15s;
}

.canvas-history__restore-btn:hover:not(:disabled) {
  background: var(--color-surface-hover);
}

.canvas-history__restore-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.canvas-history__restore-icon {
  width: 14px;
  height: 14px;
}

.canvas-history__preview {
  padding: var(--space-2) var(--space-3);
  border-top: 1px solid var(--color-border);
  background: var(--color-surface-alt, var(--color-surface));
}

.canvas-history__preview-label {
  font-size: var(--font-size-xs);
  font-weight: var(--weight-semibold);
  color: var(--color-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: var(--space-1);
}

.canvas-history__preview-info {
  font-size: var(--font-size-sm);
  color: var(--color-fg);
}
</style>
