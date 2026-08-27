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
        :disabled="disabled ?? false"
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

      <div class="composer__field">
        <textarea
          ref="textareaRef"
          class="composer__textarea"
          :value="modelValue"
          :placeholder="disabled
            ? t('conversation.chatroom.reconnecting')
            : t('conversation.chatroom.composerPlaceholder')"
          :aria-label="t('conversation.chatroom.composerPlaceholder')"
          :readonly="disabled ?? false"
          :maxlength="INPUT_LIMITS.MESSAGE"
          rows="1"
          @input="onInput"
          @keydown="onKeydown"
          @keyup="onKeyup"
          @click="refreshMention"
          @blur="closeMention"
          @dragover.prevent="dragActive = true"
          @dragleave.prevent="dragActive = false"
          @drop.prevent="onDropEvent"
        />

        <!-- @mention autocomplete: summon a specific bound agent (R15.01b). -->
        <div
          v-if="mentionOpen"
          class="composer__mentions"
          role="listbox"
          :aria-label="t('conversation.chatroom.mentionListLabel')"
        >
          <button
            v-for="(a, i) in mentionMatches"
            :key="a.id"
            type="button"
            role="option"
            :aria-selected="i === activeIndex"
            class="mention-option"
            :class="{ 'mention-option--active': i === activeIndex }"
            @mousedown.prevent="selectMention(a)"
          >
            {{ a.name }}
          </button>
        </div>
      </div>

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

    <SCharCount
      :current="modelValue.length"
      :max="INPUT_LIMITS.MESSAGE"
      hide-until-near
    />

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
          v-if="u.status === 'uploading'"
          :value="Math.round(u.progress * 100)"
          size="sm"
          class="upload__bar"
        />
        <span
          v-else-if="u.status === 'ready'"
          class="upload__ready"
        >{{ t('conversation.chatroom.uploadReady') }}</span>
        <span
          v-else
          class="upload__error"
        >{{ t('conversation.chatroom.uploadErrored') }}</span>
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
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  PlusIcon,
  PaperAirplaneIcon,
  XMarkIcon,
  DocumentIcon,
  ArrowUpTrayIcon,
} from '@heroicons/vue/24/outline'
import { SButton, SProgressBar, SCharCount } from '@shared/ui'
import { INPUT_LIMITS } from '@shared/constants/inputLimits'
import type { PendingUpload } from '../composables/useChatroomAttachments'
import { useMentionAutocomplete } from '../composables/useMentionAutocomplete'
import type { MentionableAgent } from '../utils/mentions'

const props = withDefaults(
  defineProps<{
    modelValue: string
    pendingUploads: PendingUpload[]
    agents?: MentionableAgent[]
    disabled?: boolean
  }>(),
  { agents: () => [], disabled: false },
)

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
const textareaRef = ref<HTMLTextAreaElement | null>(null)
const dragActive = ref(false)

const canSend = computed(() => {
  if (props.disabled) return false
  // Block sending while any upload is still in flight: the send path only
  // attaches uploads whose id has resolved, so firing now would silently drop
  // the not-yet-ready attachment.
  if (props.pendingUploads.some((u) => u.status === 'uploading')) return false
  const hasReady = props.pendingUploads.some((u) => u.status === 'ready')
  return props.modelValue.trim().length > 0 || hasReady
})

// ---- @mention autocomplete ------------------------------------------------
const {
  open: mentionOpen,
  matches: mentionMatches,
  activeIndex,
  refresh: refreshMention,
  close: closeMention,
  select: selectMention,
  handleKeydown: handleMentionKeydown,
  handleKeyup: onKeyup,
} = useMentionAutocomplete({
  textarea: textareaRef,
  agents: () => props.agents,
  // Clamp here too: maxlength only bounds typing, not this programmatic set,
  // so a mention inserted near the limit could otherwise overflow it.
  onInsert: (value) => emit('update:modelValue', value.slice(0, INPUT_LIMITS.MESSAGE)),
})

function onKeydown(e: KeyboardEvent): void {
  if (handleMentionKeydown(e)) return
  // Enter submits, unless it's confirming an IME composition (important for
  // CJK input) or combined with a modifier (Shift+Enter inserts a newline).
  if (
    e.key === 'Enter' &&
    !e.shiftKey &&
    !e.ctrlKey &&
    !e.metaKey &&
    !e.altKey &&
    !e.isComposing
  ) {
    e.preventDefault()
    // Mirror the send button's gate so Enter can't bypass the in-flight /
    // empty-message guard.
    if (canSend.value) emit('submit')
    return
  }
  if (e.key === 'Escape') {
    emit('update:modelValue', '')
  }
}

// ---- auto-grow ------------------------------------------------------------
// 07-conversation.md:669-671 sizes the box between 36px and 192px. Driven from
// JavaScript rather than `field-sizing: content`, which is Chromium-only and
// above the browser floor in 11-responsive-a11y.md:346-347.
const MAX_TEXTAREA_H = 192

function resize(): void {
  const el = textareaRef.value
  if (!el) return
  // Reset first: `scrollHeight` never shrinks below the current height, so
  // without this the box could grow but never shrink.
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, MAX_TEXTAREA_H)}px`
}

// The model watch is not redundant with onInput: a send clears the draft and a
// mention insert rewrites it, and neither fires an input event.
watch(() => props.modelValue, resize, { flush: 'post' })
onMounted(resize)

function onInput(e: Event): void {
  emit('update:modelValue', (e.target as HTMLTextAreaElement).value)
  emit('typing')
  refreshMention()
  resize()
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
  padding: var(--space-3) var(--space-4);
}

.composer--disabled {
  background: var(--color-danger-tint);
}

.composer--drag {
  outline: 2px dashed var(--color-accent);
  outline-offset: -4px;
}

.composer__row {
  display: flex;
  align-items: flex-end;
  gap: var(--space-2);
}

.composer__file-input {
  display: none;
}

.composer__field {
  position: relative;
  flex: 1;
  display: flex;
}

.composer__textarea {
  width: 100%;
  min-height: 36px;
  /* Backstop for the inline height that `resize()` assigns; both are needed,
     because the cap has to hold if scripting never runs. */
  max-height: 192px;
  overflow-y: auto;
  resize: none;
  border: none;
  background: transparent;
  color: var(--color-fg);
  font-size: var(--font-size-sm);
  font-family: inherit;
  line-height: var(--line-normal);
  padding: var(--space-2) 0;
}

/* Pointer focus only. This textarea is the chatroom's primary input and its
   outline used to be suppressed unconditionally, so a keyboard user had no
   indicator on it at all. */
.composer__textarea:focus:not(:focus-visible) {
  outline: none;
}

.composer__mentions {
  position: absolute;
  bottom: calc(100% + 4px);
  left: 0;
  /* On the dropdown layer, not a raw literal: the compact band's agents rail
     overlay sits at --z-dropdown and covers this menu's x-range, and a literal
     below the token scale painted the menu under it. The rail is earlier in
     the DOM, so an equal z-index resolves in the menu's favour while the user
     is typing a mention. */
  z-index: var(--z-dropdown);
  min-width: 180px;
  max-width: 280px;
  max-height: 200px;
  overflow-y: auto;
  padding: var(--space-1);
  display: flex;
  flex-direction: column;
  gap: var(--space-0-5);
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-md, 0 4px 12px rgb(0 0 0 / 12%));
}

.mention-option {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  width: 100%;
  text-align: left;
  background: none;
  border: none;
  border-radius: var(--radius-sm);
  padding: var(--space-1-5) var(--space-2);
  font-size: var(--font-size-sm);
  color: var(--color-fg);
  cursor: pointer;
}

/* Decorative accent `@` prefix — kept in CSS so the template stays free of a
   bare (untranslated) string literal. */
.mention-option::before {
  content: '@';
  color: var(--color-accent);
  font-weight: var(--weight-semibold);
}

.mention-option--active,
.mention-option:hover {
  background: var(--color-surface);
}

.composer__uploads {
  list-style: none;
  margin: var(--space-2) 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  max-height: 120px;
  overflow-y: auto;
}

.upload {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--font-size-code);
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
  font-size: var(--font-size-xs);
  color: var(--color-success);
}

.upload__error {
  font-size: var(--font-size-xs);
  color: var(--color-danger);
}

.composer__overlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  background: color-mix(in srgb, var(--color-accent) 10%, transparent);
  color: var(--color-accent);
  font-size: var(--font-size-sm);
  pointer-events: none;
}

.composer__overlay-icon {
  width: 48px;
  height: 48px;
}
</style>
