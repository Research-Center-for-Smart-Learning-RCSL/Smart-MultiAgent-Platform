<template>
  <form
    class="composer"
    :class="{ 'composer--disabled': disabled, 'composer--drag': dragActive }"
    @submit.prevent="$emit('submit')"
  >
    <div class="composer__row">
      <SButton
        type="button"
        variant="ghost"
        icon-only
        size="sm"
        :disabled="disabled"
        :aria-label="t('conversation.chatroom.attach')"
        @click="openPicker"
      >
        <PlusIcon class="w-5 h-5" />
      </SButton>
      <input
        ref="fileInput"
        type="file"
        multiple
        class="composer__file-input"
        :aria-label="t('conversation.chatroom.attach')"
        @change="onPick"
      >

      <textarea
        class="composer__textarea"
        :value="modelValue"
        :placeholder="disabled
          ? t('conversation.chatroom.reconnecting')
          : t('conversation.chatroom.composerPlaceholder')"
        :aria-label="t('conversation.chatroom.composerPlaceholder')"
        :readonly="disabled"
        rows="1"
        @input="onInput"
        @keydown.enter.exact.prevent="$emit('submit')"
        @keydown.escape="$emit('update:modelValue', '')"
        @dragover.prevent="dragActive = true"
        @dragleave.prevent="dragActive = false"
        @drop.prevent="onDropEvent"
      />

      <SButton
        type="submit"
        :variant="canSend ? 'primary' : 'ghost'"
        icon-only
        size="sm"
        :disabled="!canSend"
        :aria-label="t('conversation.chatroom.send')"
      >
        <PaperAirplaneIcon class="w-5 h-5" />
      </SButton>
    </div>

    <ul
      v-if="pendingUploads.length"
      class="composer__uploads"
    >
      <li
        v-for="u in pendingUploads"
        :key="u.id"
        class="upload"
      >
        <DocumentIcon class="upload__icon" />
        <span class="upload__name">{{ u.filename }}</span>
        <SProgressBar
          v-if="u.attachmentId === null"
          :value="Math.round(u.progress * 100)"
          size="sm"
          class="upload__bar"
        />
        <span
          v-else
          class="upload__ready"
        >{{ t('conversation.chatroom.uploadReady') }}</span>
        <SButton
          type="button"
          variant="ghost"
          icon-only
          size="sm"
          :aria-label="t('conversation.chatroom.removeUpload')"
          @click="$emit('remove-upload', u.id)"
        >
          <XMarkIcon class="w-4 h-4" />
        </SButton>
      </li>
    </ul>

    <div
      v-if="dragActive"
      class="composer__overlay"
    >
      <ArrowUpTrayIcon class="composer__overlay-icon" />
      {{ t('conversation.chatroom.dropFiles') }}
    </div>
  </form>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  PlusIcon,
  PaperAirplaneIcon,
  XMarkIcon,
  DocumentIcon,
  ArrowUpTrayIcon,
} from '@heroicons/vue/24/outline'
import { SButton, SProgressBar } from '@shared/ui'
import type { PendingUpload } from '../composables/useChatroomAttachments'

const props = defineProps<{
  modelValue: string
  pendingUploads: PendingUpload[]
  disabled?: boolean
}>()

const emit = defineEmits<{
  submit: []
  typing: []
  drop: [event: DragEvent]
  'pick-files': [files: File[]]
  'remove-upload': [id: string]
  'update:modelValue': [value: string]
}>()

const { t } = useI18n()
const fileInput = ref<HTMLInputElement | null>(null)
const dragActive = ref(false)

const canSend = computed(
  () => !props.disabled && (props.modelValue.trim().length > 0 || props.pendingUploads.length > 0),
)

function onInput(e: Event): void {
  emit('update:modelValue', (e.target as HTMLTextAreaElement).value)
  emit('typing')
}

function openPicker(): void {
  fileInput.value?.click()
}

function onPick(e: Event): void {
  const input = e.target as HTMLInputElement
  if (input.files?.length) emit('pick-files', Array.from(input.files))
  input.value = ''
}

function onDropEvent(e: DragEvent): void {
  dragActive.value = false
  emit('drop', e)
}
</script>

<style scoped>
.composer {
  position: relative;
  background: var(--color-bg);
  border-top: 1px solid var(--color-border);
  padding: 12px 16px;
}

.composer--disabled {
  background: var(--color-danger-tint, #fee2e2);
}

.composer--drag {
  outline: 2px dashed var(--color-accent);
  outline-offset: -4px;
}

.composer__row {
  display: flex;
  align-items: flex-end;
  gap: 8px;
}

.composer__file-input {
  display: none;
}

.composer__textarea {
  flex: 1;
  min-height: 36px;
  max-height: 192px;
  resize: none;
  border: none;
  background: transparent;
  color: var(--color-fg);
  font-size: 14px;
  font-family: inherit;
  line-height: 1.5;
  padding: 8px 0;
  outline: none;
}

.composer__uploads {
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 120px;
  overflow-y: auto;
}

.upload {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.upload__icon {
  width: 16px;
  height: 16px;
  color: var(--color-muted);
  flex-shrink: 0;
}

.upload__name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.upload__bar {
  width: 96px;
  flex-shrink: 0;
}

.upload__ready {
  font-size: 12px;
  color: var(--color-success);
}

.composer__overlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  background: color-mix(in srgb, var(--color-accent) 10%, transparent);
  color: var(--color-accent);
  font-size: 14px;
  pointer-events: none;
}

.composer__overlay-icon {
  width: 48px;
  height: 48px;
}
</style>
