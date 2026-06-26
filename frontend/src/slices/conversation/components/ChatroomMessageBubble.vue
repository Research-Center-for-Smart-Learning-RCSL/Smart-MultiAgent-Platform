<template>
  <!-- System messages: centered, compact, no bubble. -->
  <li
    v-if="message.sender_type === 'system'"
    :id="`msg-${message.id}`"
    class="sys"
    :class="{ 'msg--flash': flash }"
  >
    <span class="sys__line" />
    <span
      class="sys__text"
      v-html="html"
    />
    <span class="sys__line" />
  </li>

  <!-- User / agent message bubble. -->
  <li
    v-else
    :id="`msg-${message.id}`"
    class="bubble-row"
    :class="{
      'bubble-row--agent': isAgent,
      'msg--flash': flash,
      'bubble-row--pending': message._status === 'sending',
    }"
  >
    <ChatroomBubbleShell :agent="isAgent">
      <template #meta>
        <SAvatar
          :name="senderName"
          size="sm"
          :class="{ 'bubble__avatar--agent': isAgent }"
        />
        <span
          class="bubble__sender"
          :class="{ 'bubble__sender--agent': isAgent }"
        >{{ senderName }}</span>
        <time class="bubble__time">{{ time }}</time>
        <span
          v-if="message._status === 'sending'"
          class="bubble__sending"
        >{{ t('conversation.chatroom.sending') }}</span>
      </template>

      <!-- Inline edit mode (own message). -->
      <div
        v-if="editing"
        class="bubble__edit"
      >
        <STextarea
          :model-value="editDraft"
          :aria-label="t('conversation.chatroom.edit')"
          :rows="3"
          @update:model-value="emit('update:editDraft', $event)"
          @keydown.escape.prevent="emit('cancel-edit')"
          @keydown.ctrl.enter.prevent="emit('save-edit')"
        />
        <div class="bubble__edit-actions">
          <SButton
            variant="primary"
            size="sm"
            @click="emit('save-edit')"
          >
            {{ t('conversation.chatroom.save') }}
          </SButton>
          <SButton
            variant="secondary"
            size="sm"
            @click="emit('cancel-edit')"
          >
            {{ t('conversation.chatroom.cancel') }}
          </SButton>
        </div>
      </div>

      <!-- Rendered markdown (single sanitiser site, see eslint allowlist). -->
      <div
        v-else
        class="bubble__body md"
        v-html="html"
      />

      <ul
        v-if="message.attachments && message.attachments.length"
        class="bubble__attachments"
      >
        <li
          v-for="att in message.attachments"
          :key="att.id"
        >
          <!-- Image attachments (incl. agent-produced charts) render inline. -->
          <AttachmentImage
            v-if="att.status === 'active' && isImage(att.mime)"
            :attachment-id="att.id"
            :filename="att.filename"
            @download="emit('download', att)"
          />
          <button
            v-else-if="att.status === 'active'"
            type="button"
            class="attachment-link"
            @click="emit('download', att)"
          >
            <PaperClipIcon class="attachment-link__icon" />
            {{ att.filename }}
          </button>
          <span
            v-else-if="att.status === 'quarantined'"
            class="attachment-gone"
          >
            <ShieldExclamationIcon class="attachment-link__icon" />
            {{ t('conversation.chatroom.attachmentQuarantined', { name: att.filename }) }}
          </span>
          <span
            v-else
            class="attachment-gone"
          >
            <ClockIcon class="attachment-link__icon" />
            {{ t('conversation.chatroom.attachmentExpired', { name: att.filename }) }}
          </span>
        </li>
      </ul>

      <span
        v-if="message.edited_at"
        class="bubble__edited"
      >{{ t('conversation.chatroom.edited') }}</span>

      <!-- RAG citations: what retrieval fed the model for this reply. -->
      <div
        v-if="isAgent && ragSources.length"
        class="bubble__sources"
      >
        <button
          type="button"
          class="bubble__sources-toggle"
          :aria-expanded="showSources"
          @click="showSources = !showSources"
        >
          <BookOpenIcon class="bubble__sources-icon" />
          {{ t('conversation.chatroom.sources', { count: ragSources.length }) }}
          <ChevronDownIcon
            class="bubble__sources-chevron"
            :class="{ 'bubble__sources-chevron--open': showSources }"
          />
        </button>
        <ul
          v-if="showSources"
          class="bubble__sources-list"
        >
          <li
            v-for="(src, i) in ragSources"
            :key="`${src.document_id}-${src.chunk_idx}-${i}`"
            class="bubble__source"
          >
            <DocumentTextIcon class="bubble__source-icon" />
            <span class="bubble__source-name">{{
              src.filename ?? t('conversation.chatroom.sourceUnknownDoc')
            }}</span>
            <span class="bubble__source-meta">
              {{ t('conversation.chatroom.sourceChunk', { idx: src.chunk_idx }) }}
              &middot; {{ formatScore(src.score) }}
            </span>
          </li>
        </ul>
      </div>
    </ChatroomBubbleShell>

    <!-- Hover actions. -->
    <div
      v-if="!editing"
      class="bubble__actions"
    >
      <button
        v-if="canEdit"
        type="button"
        class="msg-action msg-action--edit"
        @click="emit('start-edit')"
      >
        <PencilSquareIcon class="msg-action__icon" />
        {{ t('conversation.chatroom.edit') }}
      </button>
      <button
        v-if="canDelete"
        type="button"
        class="msg-action msg-action--delete"
        @click="emit('delete')"
      >
        <TrashIcon class="msg-action__icon" />
        {{ t('conversation.chatroom.delete') }}
      </button>
      <button
        type="button"
        class="msg-action msg-action--copy"
        @click="emit('copy')"
      >
        <ClipboardDocumentIcon class="msg-action__icon" />
        {{ t('conversation.chatroom.copy') }}
      </button>
    </div>
  </li>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  PaperClipIcon,
  ShieldExclamationIcon,
  ClockIcon,
  PencilSquareIcon,
  TrashIcon,
  ClipboardDocumentIcon,
  BookOpenIcon,
  ChevronDownIcon,
  DocumentTextIcon,
} from '@heroicons/vue/24/outline'
import { SAvatar, SButton, STextarea } from '@shared/ui'
import ChatroomBubbleShell from './ChatroomBubbleShell.vue'
import AttachmentImage from './AttachmentImage.vue'
import { formatTime } from '../utils/format'
import type { Attachment, DisplayMessage, RagSource } from '../types'

const props = defineProps<{
  message: DisplayMessage
  html: string
  senderName: string
  editing: boolean
  editDraft: string
  canEdit: boolean
  canDelete: boolean
  flash?: boolean
}>()

const emit = defineEmits<{
  'start-edit': []
  'save-edit': []
  'cancel-edit': []
  delete: []
  copy: []
  download: [att: Attachment]
  'update:editDraft': [value: string]
}>()

const isAgent = computed(() => props.message.sender_type === 'agent')
const time = computed(() => formatTime(props.message.created_at))
const { t } = useI18n()

// RAG citations the backend attached to this agent reply (R10.09).
const ragSources = computed<RagSource[]>(() => {
  const raw = props.message.metadata?.rag_sources
  return Array.isArray(raw) ? (raw as RagSource[]) : []
})
const showSources = ref(false)

// Persisted metadata is untyped at runtime; tolerate a drifted/partial entry
// rather than throwing a render error that would break the whole bubble.
function formatScore(score: unknown): string {
  return typeof score === 'number' ? score.toFixed(2) : '--'
}

// Image attachments (user uploads + agent-produced charts) render inline; the
// presign endpoint forces a safe inline content-type for these MIME types.
function isImage(mime: string): boolean {
  return mime.startsWith('image/')
}
</script>

<style scoped>
.bubble-row {
  display: flex;
  flex-direction: column;
  /* Own (user) messages sit on the right; agent messages override to the left
     below. System messages use the separate .sys layout, so this only ever
     applies to user/agent bubbles. */
  align-items: flex-end;
  gap: 4px;
  margin-bottom: 8px;
}

.bubble-row--agent {
  align-items: flex-start;
}

/* Transient highlight when jumped to from search. Animates from the warning
   tint back to the bubble's natural background. */
.bubble-row.msg--flash :deep(.bubble) {
  animation: msg-flash 1.6s ease-out;
}

.sys.msg--flash .sys__text {
  animation: msg-flash 1.6s ease-out;
}

@keyframes msg-flash {
  from {
    background-color: var(--color-warning-tint, #fef3c7);
  }
}

.bubble__avatar--agent {
  box-shadow: 0 0 0 2px var(--color-accent);
  border-radius: var(--radius-full);
}

.bubble__sender {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-fg);
}

.bubble__sender--agent {
  color: var(--color-accent);
}

.bubble__time {
  margin-left: auto;
  font-size: 12px;
  color: var(--color-muted);
}

.bubble-row--pending {
  opacity: 0.6;
}

.bubble__sending {
  font-size: 11px;
  font-style: italic;
  color: var(--color-muted);
}

.bubble__body {
  font-size: 14px;
  line-height: 1.5;
  color: var(--color-fg);
  word-break: break-word;
}

.bubble__attachments {
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.attachment-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: none;
  border: none;
  padding: 0;
  font-size: 13px;
  color: var(--color-accent);
  cursor: pointer;
}

.attachment-link__icon {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
}

.attachment-gone {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  font-style: italic;
  color: var(--color-muted);
  text-decoration: line-through;
}

.bubble__edited {
  display: block;
  margin-top: 4px;
  text-align: right;
  font-size: 11px;
  font-style: italic;
  color: var(--color-muted);
}

.bubble__sources {
  margin-top: 8px;
  border-top: 1px solid var(--color-border);
  padding-top: 6px;
}

.bubble__sources-toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: none;
  border: none;
  padding: 2px 0;
  font-size: 12px;
  color: var(--color-muted);
  cursor: pointer;
}

.bubble__sources-icon {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
}

.bubble__sources-chevron {
  width: 12px;
  height: 12px;
  transition: transform var(--transition-fast);
}

.bubble__sources-chevron--open {
  transform: rotate(180deg);
}

.bubble__sources-list {
  list-style: none;
  margin: 4px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.bubble__source {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--color-fg);
}

.bubble__source-icon {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
  color: var(--color-muted);
}

.bubble__source-name {
  font-weight: 500;
  word-break: break-word;
}

.bubble__source-meta {
  color: var(--color-muted);
  font-variant-numeric: tabular-nums;
}

.bubble__edit {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.bubble__edit-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.bubble__actions {
  display: flex;
  gap: 8px;
  opacity: 0;
  transition: opacity var(--transition-fast);
}

.bubble-row:hover .bubble__actions,
.bubble-row:focus-within .bubble__actions {
  opacity: 1;
}

.msg-action {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: none;
  border: none;
  padding: 2px 4px;
  min-height: 28px;
  font-size: 12px;
  color: var(--color-muted);
  cursor: pointer;
}

.msg-action--edit {
  color: var(--color-accent);
}

.msg-action--delete {
  color: var(--color-danger);
}

.msg-action__icon {
  width: 14px;
  height: 14px;
}

/* System message */
.sys {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 12px auto;
  max-width: 60%;
}

.sys__line {
  flex: 1;
  height: 1px;
  background: var(--color-border);
}

.sys__text {
  font-size: 12px;
  font-style: italic;
  color: var(--color-muted);
}
</style>
