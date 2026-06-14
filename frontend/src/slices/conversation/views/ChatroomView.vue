<template>
  <section
    class="chatroom"
    :class="{ 'chatroom--mobile': isMobile }"
  >
    <header>
      <h1>#{{ chatroomId.slice(0, 8) }}</h1>
      <span :class="['pill', connected ? 'on' : 'off']">
        {{ connected ? $t('conversation.chatroom.live') : $t('conversation.chatroom.offline') }}
      </span>
      <input
        v-model="searchQuery"
        :placeholder="$t('conversation.chatroom.searchPlaceholder')"
        @keyup.enter="runSearch"
      >
      <button @click="runExport">
        {{ $t('conversation.chatroom.export') }}
      </button>
    </header>

    <!-- Live approval cards (G.10) — rendered above messages when present. -->
    <div
      v-if="liveApprovals.length"
      class="approvals"
    >
      <ApprovalCard
        v-for="a in liveApprovals"
        :key="a.id"
        :approval="a"
        :agent-names="{}"
      />
    </div>

    <ol
      ref="listRef"
      class="messages"
    >
      <li
        v-for="m in messages"
        :key="m.id"
      >
        <div class="meta">
          <span>{{ m.sender_type }}</span>
          <time>{{ m.created_at }}</time>
        </div>
        <!-- Single v-html site per R24.41. -->
        <div
          class="md"
          v-html="rendered[m.id]"
        />
      </li>
      <!-- Transient streaming draft: agent.token deltas accumulate here until
           the persisted reply arrives via message.created (also rendered
           through renderMarkdown → DOMPurify, same XSS contract). -->
      <li
        v-if="streamingHtml"
        class="streaming"
        data-testid="streaming-draft"
      >
        <div class="meta">
          <span class="streaming-label">{{ $t('conversation.chatroom.agentStreaming') }}</span>
        </div>
        <div
          class="md"
          v-html="streamingHtml"
        />
      </li>
    </ol>

    <aside
      v-if="!isMobile"
      class="presence"
    >
      <h4>{{ $t('conversation.chatroom.online') }}</h4>
      <ul>
        <li
          v-for="uid in presenceList"
          :key="uid"
        >
          {{ uid.slice(0, 8) }}
        </li>
      </ul>
      <p v-if="store.agentThinking[chatroomId]">
        {{ $t('conversation.chatroom.agentThinking') }}
      </p>
    </aside>

    <form
      class="composer"
      @submit.prevent="onSend"
    >
      <textarea
        v-model="draft"
        :placeholder="$t('conversation.chatroom.composerPlaceholder')"
        :aria-label="$t('conversation.chatroom.composerPlaceholder')"
        @dragover.prevent
        @drop.prevent="onDrop"
      />
      <ul
        v-if="pendingUploads.length"
        class="attachments"
      >
        <li
          v-for="a in pendingUploads"
          :key="a.id"
        >
          {{ a.filename }} ({{ Math.round(a.progress * 100) }}%)
        </li>
      </ul>
      <button type="submit">
        {{ $t('conversation.chatroom.send') }}
      </button>
    </form>

    <section
      v-if="searchHits.length"
      class="search-results"
    >
      <h4>{{ $t('conversation.chatroom.searchResults') }}</h4>
      <ul>
        <li
          v-for="h in searchHits"
          :key="h.message_id"
          v-html="renderedSnippets[h.message_id]"
        />
      </ul>
    </section>
  </section>
</template>

<script setup lang="ts">
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  onUpdated,
  reactive,
  ref,
  useTemplateRef,
  watch,
} from 'vue'
import { useRoute } from 'vue-router'

import { ElMessage } from 'element-plus'
import { useI18n } from 'vue-i18n'
import { useBreakpoint } from '@shared/composables'
import {
  createExport,
  listMessages,
  searchMessages,
  sendMessage,
  compactChatroom,
} from '../api'
import { tusUpload, resourceToAttachmentId } from '@shared/transport'
import { useChatroomSocket } from '../composables/useChatroomSocket'
import { enhanceRenderedMarkdown, renderMarkdown, sanitizeSnippet } from '../lib/renderMarkdown'
import { convKeys } from '../queries'
import { useConversationStore } from '../stores/conversation'
import type { Message, SearchHit } from '../types'
import { ApprovalCard, useOrchestrationStore } from '@slices/workflow'

const { t } = useI18n()
const route = useRoute()
const qc = useQueryClient()
const store = useConversationStore()
const chatroomId = route.params.chatroomId as string
const projectId = (route.params.projectId as string) || ''

const { isMobile } = useBreakpoint()
store.setActive(chatroomId)

const listRef = useTemplateRef<HTMLElement>('listRef')
const draft = ref('')
const searchQuery = ref('')
const searchHits = ref<SearchHit[]>([])
const renderedSnippets = computed<Record<string, string>>(() => {
  const out: Record<string, string> = {}
  for (const h of searchHits.value) out[h.message_id] = sanitizeSnippet(h.snippet)
  return out
})

const query = useQuery({
  queryKey: convKeys.messages(chatroomId),
  queryFn: () => listMessages(chatroomId, { limit: 100 }),
})
const messages = computed<Message[]>(() =>
  [...(query.data.value ?? [])].sort((a, b) =>
    a.created_at < b.created_at ? -1 : 1,
  ),
)

const rendered = reactive<Record<string, string>>({})
const renderedSources = new Map<string, string>()
watch(
  messages,
  (list) => {
    const seen = new Set<string>()
    for (const m of list) {
      seen.add(m.id)
      if (renderedSources.get(m.id) === m.content_md) continue
      rendered[m.id] = renderMarkdown(m.content_md)
      renderedSources.set(m.id, m.content_md)
    }
    for (const id of Object.keys(rendered)) {
      if (!seen.has(id)) {
        delete rendered[id]
        renderedSources.delete(id)
      }
    }
  },
  { immediate: true },
)

const { connected } = useChatroomSocket(chatroomId)
const orchStore = useOrchestrationStore()

// Streaming draft bubble (agent.token accumulation) — sanitised exactly like
// persisted messages.
const streamingHtml = computed(() => {
  const text = store.agentStream[chatroomId]
  return text ? renderMarkdown(text) : ''
})

// Agent failure surfaced by the socket layer: backend agent.finished{error}
// or the client-side thinking watchdog ('timeout'). Toast once, then clear.
watch(
  () => store.agentError[chatroomId],
  (err) => {
    if (!err) return
    ElMessage.error(
      t(
        err === 'timeout'
          ? 'conversation.chatroom.agentTimeout'
          : 'conversation.chatroom.agentFailed',
      ),
    )
    store.setAgentError(chatroomId, null)
  },
)

const presenceList = computed(() => {
  const set = store.presence[chatroomId]
  return set ? Array.from(set) : []
})

const liveApprovals = computed(() => orchStore.getApprovalsForRoom(chatroomId))

interface PendingUpload {
  id: string
  filename: string
  progress: number
  attachmentId: string | null
}
const pendingUploads = ref<PendingUpload[]>([])

async function onDrop(ev: DragEvent): Promise<void> {
  const files = Array.from(ev.dataTransfer?.files ?? [])
  for (const file of files) {
    const record: PendingUpload = {
      id: crypto.randomUUID(),
      filename: file.name,
      progress: 0,
      attachmentId: null,
    }
    pendingUploads.value.push(record)
    try {
      const result = await tusUpload({
        file,
        purpose: 'chat_attachment',
        projectId,
        chatroomId,
        onProgress: (done, total) => {
          record.progress = total === 0 ? 1 : done / total
        },
      })
      record.attachmentId = resourceToAttachmentId(result.resourceHeader)
    } catch {
      record.filename = t('conversation.chatroom.uploadFailed', { filename: record.filename })
    }
  }
}

async function onSend(): Promise<void> {
  const text = draft.value.trim()
  if (!text && pendingUploads.value.length === 0) return

  // /compact slash command (G.10): forces compaction on active agent.
  if (text === '/compact') {
    draft.value = ''
    await compactChatroom(chatroomId)
    return
  }

  const attachmentIds = pendingUploads.value
    .map((p) => p.attachmentId)
    .filter((x): x is string => !!x)
  try {
    await sendMessage(chatroomId, {
      content_md: text,
      attachment_ids: attachmentIds,
    })
    draft.value = ''
    pendingUploads.value = []
    await qc.invalidateQueries({ queryKey: convKeys.messages(chatroomId) })
    await nextTick()
    listRef.value?.scrollTo({ top: listRef.value.scrollHeight })
  } catch {
    ElMessage.error(t('conversation.chatroom.sendFailed'))
  }
}

async function runSearch(): Promise<void> {
  if (!searchQuery.value.trim()) {
    searchHits.value = []
    return
  }
  try {
    const res = await searchMessages(chatroomId, searchQuery.value.trim())
    searchHits.value = res.hits
  } catch {
    ElMessage.error(t('conversation.chatroom.searchFailed'))
  }
}

async function runExport(): Promise<void> {
  try {
    const { job_id } = await createExport(chatroomId)
    ElMessage.success(t('conversation.chatroom.exportQueued', { jobId: job_id.slice(0, 8) }))
  } catch {
    ElMessage.error(t('conversation.chatroom.exportFailed'))
  }
}

// KaTeX/Mermaid post-processing (FE-12). `onUpdated` fires on every reactive
// change — each presence blip, typing indicator, new message — and the
// Mermaid pass is async, so naive invocation lets overlapping runs race over
// the same DOM nodes. We debounce so a burst collapses to one pass, and an
// in-flight guard serialises runs; a change arriving mid-pass queues exactly
// one follow-up so the latest DOM is always reflected.
const ENHANCE_DEBOUNCE_MS = 120
let enhanceTimer: ReturnType<typeof setTimeout> | null = null
let enhanceInFlight = false
let enhanceQueued = false

async function runEnhance(): Promise<void> {
  if (enhanceInFlight) {
    enhanceQueued = true
    return
  }
  if (!listRef.value) return
  enhanceInFlight = true
  try {
    await enhanceRenderedMarkdown(listRef.value)
  } catch {
    // Best-effort; rendering errors must not crash the chatroom.
  } finally {
    enhanceInFlight = false
  }
  if (enhanceQueued) {
    enhanceQueued = false
    scheduleEnhance()
  }
}

function scheduleEnhance(): void {
  if (enhanceTimer !== null) clearTimeout(enhanceTimer)
  enhanceTimer = setTimeout(() => {
    enhanceTimer = null
    void runEnhance()
  }, ENHANCE_DEBOUNCE_MS)
}

onMounted(scheduleEnhance)
onUpdated(scheduleEnhance)
onBeforeUnmount(() => {
  if (enhanceTimer !== null) clearTimeout(enhanceTimer)
})
</script>

<style scoped>
.chatroom {
  display: grid;
  grid-template-columns: 1fr 240px;
  grid-template-rows: auto 1fr auto auto;
  gap: 0.5rem;
  height: 100%;
}
.messages {
  grid-column: 1;
  grid-row: 2;
  overflow-y: auto;
  list-style: none;
  padding: 0.5rem;
}
.presence {
  grid-column: 2;
  grid-row: 2 / 4;
}
.composer {
  grid-column: 1;
  grid-row: 3;
}
.search-results {
  grid-column: 1 / 3;
  grid-row: 4;
}
.pill.on {
  color: #0a0;
}
.pill.off {
  color: #a00;
}
.chatroom--mobile {
  grid-template-columns: 1fr;
}
.chatroom--mobile .presence {
  display: none;
}
</style>
