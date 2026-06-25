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
    :class="{ 'bubble-row--agent': isAgent, 'msg--flash': flash }"
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
          <button
            v-if="att.status === 'active'"
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
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  PaperClipIcon,
  ShieldExclamationIcon,
  ClockIcon,
  PencilSquareIcon,
  TrashIcon,
  ClipboardDocumentIcon,
} from '@heroicons/vue/24/outline'
import { SAvatar, SButton, STextarea } from '@shared/ui'
import ChatroomBubbleShell from './ChatroomBubbleShell.vue'
import { formatTime } from '../utils/format'
import type { Attachment, Message } from '../types'

const props = defineProps<{
  message: Message
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
</script>

<style scoped>
.bubble-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 8px;
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
