<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { useConfirmDialog } from '@shared/composables'
import { isProblemWithType } from '@shared/transport'
import { SAlert, SButton, SLoadingSpinner, STextarea } from '@shared/ui'

import { promptStudioApi } from '../api'
import { usePromptAssistantSocket, type AssistantMessage } from '../composables/usePromptAssistantSocket'
import { useResolvedAssistantQuery } from '../queries'

const props = defineProps<{
  projectId: string
  currentDraft: string
}>()

const emit = defineEmits<{
  applyDraft: [text: string]
}>()

const { t } = useI18n()
const { confirm } = useConfirmDialog()

const resolved = useResolvedAssistantQuery(() => props.projectId)
const available = computed(() => resolved.data.value?.available === true)

const sessionId = ref<string | null>(null)
const { messages, streamingText, streaming, errorCode, sessionExpired, pushUserMessage } =
  usePromptAssistantSocket(sessionId)

const input = ref('')
const sending = ref(false)
const listEl = ref<HTMLElement | null>(null)

async function ensureSession(): Promise<string | null> {
  // Ignore a cached id once its session has expired (AC-6) -- reusing it would
  // just 404 the POST again. Creating a fresh one is the documented recovery.
  if (sessionId.value && !sessionExpired.value) return sessionId.value
  try {
    const created = await promptStudioApi.createSession(props.projectId)
    sessionId.value = created.session_id
    return created.session_id
  } catch {
    errorCode.value = 'prompt-studio/unavailable'
    return null
  }
}

async function send(): Promise<void> {
  const content = input.value.trim()
  if (!content || sending.value) return
  sending.value = true
  try {
    const id = await ensureSession()
    if (!id) return
    pushUserMessage(content)
    input.value = ''
    await promptStudioApi.postMessage(id, { content, editor_draft: props.currentDraft })
  } catch (err) {
    if (isProblemWithType(err, 'prompt-studio/quota-exceeded')) {
      errorCode.value = 'prompt-studio/quota-exceeded'
    } else {
      errorCode.value = 'prompt-studio/turn-failed'
    }
  } finally {
    sending.value = false
  }
}

/** Extract the first fenced code block, which the wrapper prompt asks the model to emit. */
function extractDraft(text: string): string | null {
  const match = text.match(/```[^\n]*\n([\s\S]*?)```/)
  return match?.[1] ? match[1].trimEnd() : null
}

interface DisplayMessage extends AssistantMessage {
  draft: string | null
  errorText: string | null
}

/** Shared with `errorMessage` below: a recovered failure marker's `content`
 *  is the same error code the live `prompt.error` banner carries. */
function errorLabel(code: string): string {
  if (code === 'prompt-studio/quota-exceeded') return t('promptStudio.panel.quotaError')
  if (code === 'prompt-studio/timeout') return t('promptStudio.panel.timeoutError')
  return t('promptStudio.panel.turnError')
}

// Computed once per pushed message (messages.value only changes reference on
// push), not on every render -- streamingText updates on every WS token and
// would otherwise re-run extractDraft over every prior message each time.
const displayMessages = computed<DisplayMessage[]>(() =>
  messages.value.map((m) => ({
    ...m,
    draft: m.role === 'assistant' && !m.error ? extractDraft(m.content) : null,
    errorText: m.error ? errorLabel(m.content) : null,
  })),
)

async function apply(draft: string): Promise<void> {
  if (props.currentDraft.trim().length > 0) {
    const ok = await confirm({
      title: t('promptStudio.panel.applyConfirmTitle'),
      message: t('promptStudio.panel.applyConfirmBody'),
      variant: 'warning',
      confirmLabel: t('promptStudio.panel.applyConfirm'),
    })
    if (!ok) return
  }
  emit('applyDraft', draft)
}

const errorMessage = computed(() => (errorCode.value ? errorLabel(errorCode.value) : null))

watch([messages, streamingText], async () => {
  await nextTick()
  listEl.value?.scrollTo({ top: listEl.value.scrollHeight })
})
</script>

<template>
  <div class="flex h-full flex-col rounded border border-[var(--color-border)]">
    <div class="border-b border-[var(--color-border)] px-3 py-2 text-sm font-semibold">
      {{ t('promptStudio.panel.title') }}
    </div>

    <div
      v-if="resolved.isLoading.value"
      class="flex flex-1 items-center justify-center p-4"
    >
      <SLoadingSpinner size="sm" />
    </div>

    <div
      v-else-if="!available"
      class="flex flex-1 items-center justify-center p-4 text-center text-sm text-[var(--color-muted)]"
    >
      {{ t('promptStudio.panel.unavailable') }}
    </div>

    <template v-else>
      <div
        ref="listEl"
        class="flex-1 space-y-3 overflow-y-auto p-3"
      >
        <p
          v-if="!messages.length && !streaming"
          class="text-center text-sm text-[var(--color-muted)]"
        >
          {{ t('promptStudio.panel.intro') }}
        </p>
        <div
          v-for="(m, i) in displayMessages"
          :key="i"
          class="flex"
          :class="m.role === 'user' ? 'justify-end' : 'justify-start'"
        >
          <div
            class="max-w-[85%] whitespace-pre-wrap rounded px-3 py-2 text-sm"
            :class="[
              m.role === 'user' ? 'bg-[var(--color-primary-soft)]' : 'bg-[var(--color-surface-2)]',
              { 'italic text-[var(--color-muted)]': m.errorText },
            ]"
          >
            {{ m.errorText ?? m.content }}
            <div
              v-if="m.draft"
              class="mt-2"
            >
              <SButton
                variant="secondary"
                size="sm"
                type="button"
                @click="apply(m.draft)"
              >
                {{ t('promptStudio.panel.applyDraft') }}
              </SButton>
            </div>
          </div>
        </div>
        <div
          v-if="streaming"
          class="flex justify-start"
        >
          <div class="max-w-[85%] whitespace-pre-wrap rounded bg-[var(--color-surface-2)] px-3 py-2 text-sm">
            {{ streamingText }}<span class="animate-pulse">▍</span>
          </div>
        </div>
      </div>

      <SAlert
        v-if="sessionExpired"
        variant="info"
        class="mx-3"
      >
        {{ t('promptStudio.panel.sessionExpired') }}
      </SAlert>

      <SAlert
        v-if="errorMessage"
        variant="warning"
        class="mx-3"
      >
        {{ errorMessage }}
      </SAlert>

      <div class="border-t border-[var(--color-border)] p-3">
        <STextarea
          v-model="input"
          :rows="2"
          :maxlength="32000"
          :placeholder="t('promptStudio.panel.composerPlaceholder')"
          @keydown.enter.exact.prevent="send"
        />
        <div class="mt-2 flex justify-end">
          <SButton
            variant="primary"
            size="sm"
            :loading="sending || streaming"
            :disabled="!input.trim()"
            type="button"
            @click="send"
          >
            {{ t('promptStudio.panel.send') }}
          </SButton>
        </div>
      </div>
    </template>
  </div>
</template>
