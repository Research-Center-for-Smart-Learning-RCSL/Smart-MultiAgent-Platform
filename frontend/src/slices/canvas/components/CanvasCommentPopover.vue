<script setup lang="ts">
import { ref, computed, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { ChatBubbleLeftIcon, PencilIcon, TrashIcon, XMarkIcon } from '@heroicons/vue/24/outline'
import { useConfirmDialog } from '@shared/composables/useConfirmDialog'
import { useSessionStore } from '@shared/stores/session'
import type { CanvasComment } from '../types'

const { t } = useI18n()
const { confirm } = useConfirmDialog()
const session = useSessionStore()

defineProps<{
  comments: CanvasComment[]
  isLoading: boolean
}>()

const emit = defineEmits<{
  create: [content: string]
  update: [commentId: string, content: string]
  delete: [commentId: string]
  close: []
}>()

const newComment = ref('')
const editingId = ref<string | null>(null)
const editContent = ref('')
const inputRef = ref<HTMLTextAreaElement>()

const currentUserId = computed(() => session.me?.id ?? null)

function isOwnComment(comment: CanvasComment): boolean {
  return (
    currentUserId.value !== null && comment.created_by_user_id === currentUserId.value
  )
}

function handleSubmit() {
  const text = newComment.value.trim()
  if (!text) return
  emit('create', text)
  newComment.value = ''
}

function startEdit(comment: CanvasComment) {
  editingId.value = comment.id
  editContent.value = comment.content
  nextTick(() => {
    const el = document.querySelector<HTMLTextAreaElement>('.comment-edit-input')
    el?.focus()
  })
}

function cancelEdit() {
  editingId.value = null
  editContent.value = ''
}

function submitEdit() {
  const text = editContent.value.trim()
  if (!text || !editingId.value) return
  emit('update', editingId.value, text)
  editingId.value = null
  editContent.value = ''
}

async function handleDelete(commentId: string) {
  const confirmed = await confirm({
    title: t('canvas.deleteComment'),
    message: t('canvas.deleteCommentConfirm'),
    variant: 'warning',
  })
  if (confirmed) {
    emit('delete', commentId)
  }
}

function formatTime(iso: string): string {
  const d = new Date(iso)
  const now = new Date()
  const diffMs = now.getTime() - d.getTime()
  const diffMin = Math.floor(diffMs / 60000)
  if (diffMin < 1) return 'just now'
  if (diffMin < 60) return `${diffMin}m ago`
  const diffHr = Math.floor(diffMin / 60)
  if (diffHr < 24) return `${diffHr}h ago`
  const diffDays = Math.floor(diffHr / 24)
  return `${diffDays}d ago`
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSubmit()
  }
}

function handleEditKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    submitEdit()
  }
  if (e.key === 'Escape') {
    cancelEdit()
  }
}
</script>

<template>
  <div class="comment-popover">
    <div class="comment-popover__header">
      <ChatBubbleLeftIcon class="comment-popover__icon" />
      <span class="comment-popover__title">{{ t('canvas.comments') }}</span>
      <span
        v-if="comments.length"
        class="comment-popover__count"
      >{{ comments.length }}</span>
      <button
        class="comment-popover__close"
        @click="emit('close')"
      >
        <XMarkIcon class="comment-popover__close-icon" />
      </button>
    </div>

    <div class="comment-popover__body">
      <div
        v-if="!comments.length && !isLoading"
        class="comment-popover__empty"
      >
        {{ t('canvas.noComments') }}
      </div>

      <div
        v-for="comment in comments"
        :key="comment.id"
        class="comment-item"
      >
        <div class="comment-item__header">
          <span class="comment-item__author">
            {{ comment.created_by_user_id?.slice(0, 8) ?? 'guest' }}
          </span>
          <span class="comment-item__time">{{ formatTime(comment.created_at) }}</span>
          <div
            v-if="isOwnComment(comment)"
            class="comment-item__actions"
          >
            <button
              :title="t('canvas.editComment')"
              @click="startEdit(comment)"
            >
              <PencilIcon class="comment-item__action-icon" />
            </button>
            <button
              :title="t('canvas.deleteComment')"
              @click="handleDelete(comment.id)"
            >
              <TrashIcon class="comment-item__action-icon" />
            </button>
          </div>
        </div>

        <div v-if="editingId === comment.id">
          <textarea
            v-model="editContent"
            class="comment-edit-input"
            :aria-label="t('canvas.editComment')"
            maxlength="2000"
            rows="2"
            @keydown="handleEditKeydown"
          />
          <div class="comment-edit-actions">
            <button
              class="comment-edit-actions__save"
              @click="submitEdit"
            >
              {{ t('common.save') }}
            </button>
            <button
              class="comment-edit-actions__cancel"
              @click="cancelEdit"
            >
              {{ t('common.cancel') }}
            </button>
          </div>
        </div>
        <p
          v-else
          class="comment-item__text"
        >
          {{ comment.content }}
        </p>
      </div>
    </div>

    <div class="comment-popover__input">
      <textarea
        ref="inputRef"
        v-model="newComment"
        class="comment-popover__textarea"
        :aria-label="t('canvas.addComment')"
        :placeholder="t('canvas.commentPlaceholder')"
        maxlength="2000"
        rows="2"
        @keydown="handleKeydown"
      />
      <button
        class="comment-popover__submit"
        :disabled="!newComment.trim()"
        @click="handleSubmit"
      >
        {{ t('canvas.addComment') }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.comment-popover {
  display: flex;
  flex-direction: column;
  width: 320px;
  max-height: 480px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  box-shadow: var(--elevation-md);
  overflow: hidden;
}

.comment-popover__header {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid var(--color-border);
}

.comment-popover__icon {
  width: 16px;
  height: 16px;
  color: var(--color-muted);
}

.comment-popover__title {
  font-size: var(--font-size-sm);
  font-weight: var(--weight-semibold);
}

.comment-popover__count {
  font-size: var(--font-size-xs);
  color: var(--color-muted);
}

.comment-popover__close {
  margin-left: auto;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: none;
  background: transparent;
  color: var(--color-muted);
  cursor: pointer;
  border-radius: var(--radius-sm);
}

.comment-popover__close:hover {
  background: var(--color-surface-hover);
}

.comment-popover__close-icon {
  width: 14px;
  height: 14px;
}

.comment-popover__body {
  flex: 1;
  overflow-y: auto;
  padding: var(--space-2) var(--space-3);
}

.comment-popover__empty {
  font-size: var(--font-size-sm);
  color: var(--color-muted);
  text-align: center;
  padding: var(--space-4) 0;
}

.comment-item {
  padding: var(--space-2) 0;
}

.comment-item + .comment-item {
  border-top: 1px solid var(--color-border-light, var(--color-border));
}

.comment-item__header {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  margin-bottom: var(--space-1);
}

.comment-item__author {
  font-size: var(--font-size-xs);
  font-weight: var(--weight-medium);
  color: var(--color-fg);
}

.comment-item__time {
  font-size: var(--font-size-xs);
  color: var(--color-muted);
}

.comment-item__actions {
  margin-left: auto;
  display: flex;
  gap: var(--space-1);
}

.comment-item__actions button {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border: none;
  background: transparent;
  color: var(--color-muted);
  cursor: pointer;
  border-radius: var(--radius-sm);
}

.comment-item__actions button:hover {
  background: var(--color-surface-hover);
  color: var(--color-fg);
}

.comment-item__action-icon {
  width: 14px;
  height: 14px;
}

.comment-item__text {
  font-size: var(--font-size-sm);
  line-height: 1.5;
  color: var(--color-fg);
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
}

.comment-edit-input {
  width: 100%;
  resize: vertical;
  font-size: var(--font-size-sm);
  padding: var(--space-1);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  color: var(--color-fg);
  font-family: inherit;
}

.comment-edit-actions {
  display: flex;
  gap: var(--space-1);
  margin-top: var(--space-1);
}

.comment-edit-actions__save,
.comment-edit-actions__cancel {
  font-size: var(--font-size-xs);
  padding: var(--space-0-5, 2px) var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  color: var(--color-fg);
  cursor: pointer;
}

.comment-edit-actions__save:hover,
.comment-edit-actions__cancel:hover {
  background: var(--color-surface-hover);
}

.comment-popover__input {
  padding: var(--space-2) var(--space-3);
  border-top: 1px solid var(--color-border);
}

.comment-popover__textarea {
  width: 100%;
  resize: none;
  font-size: var(--font-size-sm);
  padding: var(--space-1) var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  color: var(--color-fg);
  font-family: inherit;
}

.comment-popover__textarea:focus {
  outline: 2px solid var(--color-primary);
  outline-offset: -1px;
}

.comment-popover__submit {
  margin-top: var(--space-1);
  width: 100%;
  font-size: var(--font-size-sm);
  padding: var(--space-1) var(--space-2);
  border: none;
  border-radius: var(--radius-sm);
  background: var(--color-primary);
  color: white;
  cursor: pointer;
  font-weight: var(--weight-medium);
}

.comment-popover__submit:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.comment-popover__submit:not(:disabled):hover {
  opacity: 0.9;
}
</style>
