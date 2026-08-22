<template>
  <!-- Released observation (SRS §28): a creator-published analysis. Full-width
       flat card, attributed to the room owner, not a chat bubble. -->
  <li
    v-if="isReleasedObservation"
    :id="`msg-${message.id}`"
    class="released"
    :class="{ 'msg--flash': flash }"
  >
    <p class="released__head">
      {{ releasedHeader }}
    </p>
    <!-- Rendered via renderMarkdown() → DOMPurify (same contract as bubbles). -->
    <!-- eslint-disable-next-line vue/no-v-html -->
    <div
      class="released__body"
      v-html="html"
    />
  </li>

  <!-- System messages: centered, compact, no bubble. -->
  <li
    v-else-if="message.sender_type === 'system'"
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
          <!-- Raster images the backend will serve inline (incl. agent-produced
               charts) render inline; everything else takes the chip below. -->
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
  // agent_id -> display name, for resolving a released observation's
  // observer_agent_id (R28.06) to something readable. Optional: most
  // messages never touch it, and the fallback below still degrades to a
  // truncated id when a name isn't resolvable.
  agentNames?: Record<string, string>
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

// A creator-released observation (R28.06): a system message tagged in metadata.
// Metadata is untyped at runtime — guard defensively so a drifted entry falls
// back to the plain system divider instead of throwing.
const isReleasedObservation = computed(
  () =>
    props.message.sender_type === 'system' &&
    props.message.metadata?.type === 'released_observation',
)
const releasedHeader = computed(() => {
  const observer = props.message.metadata?.observer_agent_id
  // Observer name is present only when the room disclosed observers at release
  // time (R28.09); otherwise attribute to the owner alone. The metadata only
  // ever carries the agent's id, so resolve it against agentNames the same
  // way every other sender label does — falling back to a truncated id when
  // the map doesn't have it (e.g. the agent was later removed from the project).
  return typeof observer === 'string' && observer
    ? t('conversation.observers.releasedByOwnerNamed', {
        name: props.agentNames?.[observer] ?? observer.slice(0, 8),
      })
    : t('conversation.observers.releasedByOwner')
})

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

// Mirrors the raster entries of the backend's _INLINE_SAFE_MIME allowlist
// (contexts/conversation/application/attachment_service.py). Anything absent
// from it -- image/svg+xml above all, which is scriptable markup -- is presigned
// as application/octet-stream with an attachment disposition by design, so an
// <img> could never decode it. Those fall through to the download chip below.
// Normalised the same way the backend normalises, so a parameter suffix cannot
// route a type past the list.
const INLINE_IMAGE_MIME = new Set([
  'image/png',
  'image/jpeg',
  'image/gif',
  'image/webp',
  'image/bmp',
])

function isImage(mime: string): boolean {
  const base = mime.split(';', 1)[0] ?? ''
  return INLINE_IMAGE_MIME.has(base.trim().toLowerCase())
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
  gap: var(--space-1);
  margin-bottom: var(--space-2);
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
    background-color: var(--color-warning-tint);
  }
}

.bubble__avatar--agent {
  box-shadow: 0 0 0 2px var(--color-accent);
  border-radius: var(--radius-full);
}

.bubble__sender {
  font-size: var(--font-size-code);
  font-weight: var(--weight-semibold);
  color: var(--color-fg);
}

.bubble__sender--agent {
  color: var(--color-accent);
}

.bubble__time {
  margin-left: auto;
  font-size: var(--font-size-xs);
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
  font-size: var(--font-size-sm);
  line-height: var(--line-normal);
  color: var(--color-fg);
  word-break: break-word;
}

.bubble__attachments {
  list-style: none;
  margin: var(--space-2) 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.attachment-link {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  background: none;
  border: none;
  padding: 0;
  font-size: var(--font-size-code);
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
  gap: var(--space-1);
  font-size: var(--font-size-code);
  font-style: italic;
  color: var(--color-muted);
  text-decoration: line-through;
}

.bubble__edited {
  display: block;
  margin-top: var(--space-1);
  text-align: right;
  font-size: 11px;
  font-style: italic;
  color: var(--color-muted);
}

.bubble__sources {
  margin-top: var(--space-2);
  border-top: 1px solid var(--color-border-subtle);
  padding-top: var(--space-1-5);
}

.bubble__sources-toggle {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  background: none;
  border: none;
  padding: var(--space-0-5) 0;
  font-size: var(--font-size-xs);
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
  margin: var(--space-1) 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-0-5);
}

.bubble__source {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  font-size: var(--font-size-xs);
  color: var(--color-fg);
}

.bubble__source-icon {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
  color: var(--color-muted);
}

.bubble__source-name {
  font-weight: var(--weight-medium);
  word-break: break-word;
}

.bubble__source-meta {
  color: var(--color-muted);
  font-variant-numeric: tabular-nums;
}

.bubble__edit {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.bubble__edit-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
}

.bubble__actions {
  display: flex;
  gap: var(--space-2);
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
  gap: var(--space-1);
  background: none;
  border: none;
  padding: var(--space-0-5) var(--space-1);
  min-height: 28px;
  font-size: var(--font-size-xs);
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
  gap: var(--space-3);
  margin: var(--space-3) auto;
  max-width: 60%;
}

.sys__line {
  flex: 1;
  height: 1px;
  background: var(--color-border);
}

.sys__text {
  font-size: var(--font-size-xs);
  font-style: italic;
  color: var(--color-muted);
}

.released {
  margin: var(--space-3) 0;
  padding: 12px 14px;
  border: 1px solid var(--color-border);
  border-left: 3px solid var(--color-accent);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  list-style: none;
}

.released__head {
  font-size: var(--font-size-xs);
  font-weight: var(--weight-semibold);
  color: var(--color-muted);
  margin: 0 0 var(--space-1-5);
}

.released__body {
  font-size: var(--font-size-sm);
  color: var(--color-fg);
  overflow-wrap: anywhere;
}
</style>
