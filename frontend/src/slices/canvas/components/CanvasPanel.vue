<script setup lang="ts">
import { ref, computed, toRef, defineAsyncComponent } from 'vue'
import { useI18n } from 'vue-i18n'
import { XMarkIcon, ChatBubbleLeftIcon } from '@heroicons/vue/24/outline'
import CanvasToolbar from './CanvasToolbar.vue'
import CanvasCommentPopover from './CanvasCommentPopover.vue'
import { useCanvasState } from '../composables/useCanvasState'
import { useCanvasSocket } from '../composables/useCanvasSocket'
import { useCanvasComments } from '../composables/useCanvasComments'
import type { CanvasObjectKind } from '../types'
import SLoadingSpinner from '@shared/ui/SLoadingSpinner.vue'
import SEmptyState from '@shared/ui/SEmptyState.vue'

const CanvasRenderer = defineAsyncComponent(() => import('./CanvasRenderer.vue'))

const { t } = useI18n()

const props = defineProps<{
  chatroomId: string
  isFullscreen: boolean
}>()

const emit = defineEmits<{
  close: []
  toggleFullscreen: []
}>()

const chatroomIdRef = toRef(props, 'chatroomId')
const {
  canvas,
  objects,
  isLoading,
  error,
  createObject,
  uploadImage,
  saveSnapshot,
  updateSettings,
  isSavingSnapshot,
} = useCanvasState(chatroomIdRef)

useCanvasSocket(chatroomIdRef)

const selectedObjectId = ref<string | null>(null)
const showComments = ref(false)

const {
  comments,
  commentCounts,
  isLoading: commentsLoading,
  createComment,
  updateComment,
  deleteComment,
} = useCanvasComments(chatroomIdRef, selectedObjectId)

const showSettings = ref(false)
const fileInputRef = ref<HTMLInputElement>()

function handleSelectObject(objectId: string) {
  selectedObjectId.value = objectId
}

function openComments(objectId: string) {
  selectedObjectId.value = objectId
  showComments.value = true
}

function closeComments() {
  showComments.value = false
}

async function handleCreateComment(content: string) {
  await createComment(content)
}

async function handleUpdateComment(commentId: string, content: string) {
  await updateComment({ commentId, content })
}

async function handleDeleteComment(commentId: string) {
  await deleteComment(commentId)
}

async function handleAddObject(kind: CanvasObjectKind) {
  await createObject({
    kind,
    position_x: 100 + Math.random() * 200,
    position_y: 100 + Math.random() * 200,
    width: kind === 'text' ? 200 : 150,
    height: kind === 'text' ? 40 : 150,
    content: kind === 'note' ? '' : kind === 'text' ? '' : null,
  })
}

function handleUploadImage() {
  fileInputRef.value?.click()
}

async function onFileSelected(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  await uploadImage(file)
  input.value = ''
}

async function handleSave() {
  await saveSnapshot()
}

function handleChange(_elements: unknown[]) {
  // Phase 1: changes are saved manually via snapshot; Phase 2 will use CRDT sync
}

const exposeToAgents = computed(() => canvas.value?.expose_to_agents ?? true)

async function toggleExposeToAgents() {
  await updateSettings({ expose_to_agents: !exposeToAgents.value })
}

function getCommentCount(objectId: string): number {
  return commentCounts.value[objectId] ?? 0
}
</script>

<template>
  <div
    class="canvas-panel"
    :class="{ 'canvas-panel--fullscreen': isFullscreen }"
  >
    <div class="canvas-panel__header">
      <span class="canvas-panel__title">{{ t('canvas.title') }}</span>
      <span
        v-if="objects.length"
        class="canvas-panel__count"
      >
        {{ objects.length }} {{ t('canvas.objects') }}
      </span>
      <button
        class="canvas-panel__close"
        :title="t('canvas.close')"
        @click="emit('close')"
      >
        <XMarkIcon class="canvas-panel__close-icon" />
      </button>
    </div>

    <CanvasToolbar
      :is-fullscreen="isFullscreen"
      :is-saving="!!isSavingSnapshot"
      @add-note="handleAddObject('note')"
      @add-text="handleAddObject('text')"
      @add-shape="handleAddObject('shape')"
      @add-connector="handleAddObject('connector')"
      @draw="handleAddObject('drawing')"
      @upload-image="handleUploadImage"
      @save="handleSave"
      @toggle-fullscreen="emit('toggleFullscreen')"
      @open-settings="showSettings = !showSettings"
    />

    <div
      v-if="showSettings"
      class="canvas-panel__settings"
    >
      <label
        for="canvas-expose-agents"
        class="canvas-panel__setting"
      >
        <input
          id="canvas-expose-agents"
          type="checkbox"
          :checked="exposeToAgents"
          @change="toggleExposeToAgents"
        >
        {{ t('canvas.exposeToAgents') }}
      </label>
    </div>

    <div class="canvas-panel__body">
      <SLoadingSpinner v-if="isLoading" />
      <div
        v-else-if="error"
        class="canvas-panel__error"
      >
        {{ t('canvas.loadError') }}
      </div>
      <SEmptyState
        v-else-if="objects.length === 0"
        :title="t('canvas.empty')"
        :description="t('canvas.emptyDescription')"
      />
      <template v-else>
        <div class="canvas-panel__canvas-area">
          <Suspense>
            <CanvasRenderer
              :objects="objects"
              @change="handleChange"
            />
            <template #fallback>
              <SLoadingSpinner />
            </template>
          </Suspense>

          <div class="canvas-panel__object-list">
            <button
              v-for="obj in objects"
              :key="obj.id"
              class="canvas-panel__object-comment-btn"
              :class="{ 'canvas-panel__object-comment-btn--active': selectedObjectId === obj.id && showComments }"
              :title="t('canvas.comments')"
              @click="openComments(obj.id)"
            >
              <ChatBubbleLeftIcon class="canvas-panel__comment-icon" />
              <span
                v-if="getCommentCount(obj.id) > 0"
                class="canvas-panel__comment-badge"
              >{{ getCommentCount(obj.id) }}</span>
            </button>
          </div>
        </div>

        <CanvasCommentPopover
          v-if="showComments && selectedObjectId"
          :comments="comments"
          :is-loading="commentsLoading"
          @create="handleCreateComment"
          @update="handleUpdateComment"
          @delete="handleDeleteComment"
          @close="closeComments"
        />
      </template>
    </div>

    <input
      ref="fileInputRef"
      type="file"
      :aria-label="t('canvas.uploadImage')"
      accept="image/png,image/jpeg,image/webp,image/svg+xml"
      hidden
      @change="onFileSelected"
    >
  </div>
</template>

<style scoped>
.canvas-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: var(--color-surface);
  border-left: 1px solid var(--color-border);
}

.canvas-panel--fullscreen {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal, 400);
  border-left: none;
}

.canvas-panel__header {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid var(--color-border);
}

.canvas-panel__title {
  font-weight: var(--weight-semibold);
  font-size: var(--font-size-sm);
}

.canvas-panel__count {
  font-size: var(--font-size-xs);
  color: var(--color-muted);
}

.canvas-panel__close {
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

.canvas-panel__close:hover {
  background: var(--color-surface-hover);
  color: var(--color-fg);
}

.canvas-panel__close-icon {
  width: 18px;
  height: 18px;
}

.canvas-panel__settings {
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid var(--color-border);
  font-size: var(--font-size-sm);
}

.canvas-panel__setting {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  cursor: pointer;
}

.canvas-panel__body {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.canvas-panel__canvas-area {
  flex: 1;
  min-height: 0;
  position: relative;
  display: flex;
  flex-direction: column;
}

.canvas-panel__error {
  color: var(--color-danger);
  font-size: var(--font-size-sm);
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 1;
}

.canvas-panel__object-list {
  display: flex;
  gap: var(--space-1);
  padding: var(--space-1) var(--space-2);
  overflow-x: auto;
  border-top: 1px solid var(--color-border);
  flex-shrink: 0;
}

.canvas-panel__object-comment-btn {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  color: var(--color-muted);
  cursor: pointer;
  border-radius: var(--radius-sm);
  flex-shrink: 0;
}

.canvas-panel__object-comment-btn:hover {
  background: var(--color-surface-hover);
  color: var(--color-fg);
}

.canvas-panel__object-comment-btn--active {
  border-color: var(--color-primary);
  color: var(--color-primary);
}

.canvas-panel__comment-icon {
  width: 14px;
  height: 14px;
}

.canvas-panel__comment-badge {
  position: absolute;
  top: -4px;
  right: -4px;
  min-width: 16px;
  height: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  font-weight: var(--weight-semibold);
  background: var(--color-primary);
  color: white;
  border-radius: 999px;
  padding: 0 4px;
}
</style>
