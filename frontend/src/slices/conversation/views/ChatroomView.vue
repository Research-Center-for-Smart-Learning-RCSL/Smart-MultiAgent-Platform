<template>
  <section
    ref="chatroomRef"
    class="chatroom"
    :class="{ 'chatroom--mobile': isMobile, 'chatroom--tablet': isTablet }"
    :style="[
      isMobile ? { '--kb-inset': `${keyboardInset}px` } : {},
      isDesktop ? { '--chatroom-rail-w': `${railWidth}px` } : {},
    ]"
  >
    <ChatroomHeader
      class="chatroom__header"
      :room-name="roomName"
      :connection-state="connectionState"
      :is-mobile="isMobile"
      :is-desktop="isDesktop"
      :observers-present="roomQuery.data.value?.observers_present ?? false"
      :can-export="!(roomQuery.data.value?.viewer_is_guest ?? false)"
      @back="goBack"
      @search="searchOpen = true"
      @settings="goSettings"
      @export="openExport"
      @toggle-agents="agentsDrawerOpen = true"
      @toggle-people="peopleDrawerOpen = true"
    />

    <ChatroomAgentSidebar
      v-if="!isMobile"
      class="chatroom__agents"
      :agents="agentList"
    />

    <div class="chatroom__feed">
      <ChatroomSearchPanel
        v-if="searchOpen"
        :query="searchQuery"
        :hits="searchHits"
        :rendered-snippets="renderedSnippets"
        :searching="searching"
        @update:query="searchQuery = $event"
        @search="doSearch"
        @close="searchOpen = false"
        @select="onSelectHit"
      />

      <ol
        ref="listRef"
        class="messages"
        role="log"
        aria-live="polite"
        aria-relevant="additions"
        :aria-label="t('conversation.chatroom.messageList')"
      >
        <li
          v-if="hasOlderMessages && !messagesPending"
          ref="loadEarlierRef"
        >
          <ChatroomLoadEarlier
            :loading="loadingOlder"
            @load="onLoadEarlier"
          />
        </li>

        <li
          v-if="messagesPending"
          class="chatroom__messages-skeleton"
        >
          <SSkeleton
            variant="rect"
            height="56px"
          />
          <SSkeleton
            variant="rect"
            height="56px"
          />
          <SSkeleton
            variant="rect"
            height="56px"
          />
        </li>

        <li v-if="messagesErrored">
          <SAlert variant="danger">
            {{ t('conversation.chatroom.loadMessagesFailed') }}
            <template #actions>
              <SButton
                variant="ghost"
                size="sm"
                @click="refetchMessages"
              >
                {{ t('conversation.chatroom.retry') }}
              </SButton>
            </template>
          </SAlert>
        </li>

        <TransitionGroup name="msg">
          <template
            v-for="item in feedItems"
            :key="item.key"
          >
            <ChatroomMessageBubble
              v-if="item.kind === 'message'"
              :message="item.message"
              :html="rendered[item.message.id] ?? ''"
              :sender-name="senderName(item.message)"
              :agent-names="agentNames"
              :editing="editingId === item.message.id"
              :edit-draft="editDraft"
              :can-edit="canEdit(item.message)"
              :can-delete="canDelete(item.message)"
              :flash="highlightId === item.message.id"
              @start-edit="startEdit(item.message)"
              @save-edit="saveEdit"
              @cancel-edit="cancelEdit"
              @delete="confirmDelete(item.message)"
              @copy="copyMessage(item.message)"
              @download="downloadAttachment"
              @update:edit-draft="editDraft = $event"
            />
            <li
              v-else
              data-testid="feed-approval"
            >
              <ApprovalCard
                :approval="item.approval"
                :agent-names="agentNames"
              />
            </li>
          </template>
        </TransitionGroup>

        <ChatroomStreamingBubble
          v-for="[agentId, html] in streamingEntries"
          :key="`stream-${agentId}`"
          :html="html"
          :agent-name="agentNames[agentId] ?? agentId.slice(0, 8)"
          aria-live="off"
        />

        <li
          v-if="!messagesPending && !messagesErrored && !feedItems.length && !streamingEntries.length"
          class="chatroom__empty"
        >
          <SEmptyState
            :icon="ChatBubbleLeftRightIcon"
            :title="t('conversation.chatroom.emptyTitle')"
            :text="t('conversation.chatroom.emptyText')"
          />
        </li>
      </ol>

      <div
        v-if="showPill"
        class="chatroom__pill"
      >
        <ChatroomNewMessagesPill
          :count="newCount"
          @click="scrollToBottom(true)"
        />
      </div>
    </div>

    <ChatroomTypingIndicator
      class="chatroom__typing"
      :names="typingNames"
    />

    <ChatroomComposer
      v-model="draft"
      class="chatroom__composer"
      :pending-uploads="pendingUploads"
      :agents="mentionables"
      @submit="send"
      @typing="emitTyping"
      @drop="onDrop"
      @pick-files="uploadFiles"
      @remove-upload="removeUpload"
    />

    <!-- Desktop right rail: tabbed People/Observer once the creator has an
         observer surface to show (a live binding or leftover observations);
         plain presence panel otherwise. -->
    <SResizeHandle
      v-if="isDesktop"
      class="chatroom__rail-handle"
      :value="railWidth"
      :min="RAIL_MIN_WIDTH"
      :max="railMaxWidth"
      :label="t('conversation.chatroom.railResize')"
      invert
      @update:value="setRailWidth"
      @reset="resetRailWidth"
    />
    <div
      v-if="isDesktop"
      class="chatroom__presence"
    >
      <STabs
        v-if="showRailTabs"
        v-model="railTab"
        :tabs="railTabs"
        fill
      >
        <template #tab-people>
          <ChatroomPresence
            :online-users="onlineUsers"
            :agents="agentList"
          />
        </template>
        <template #tab-observer>
          <ObserverPanel
            :observer-agents="observations.observerAgents.value"
            :observations="observations.observations.value"
            :loading="observations.observationsLoading.value"
            :has-more="observations.hasMore.value"
            :loading-more="observations.loadingMore.value"
            :agent-names="agentNames"
            @release="openRelease"
            @delete="onObservationDelete"
            @load-earlier="observations.loadEarlier"
          />
        </template>
        <template #tab-activity>
          <ActivityPanel
            :chatroom-id="chatroomId"
            :project-id="observerProjectId"
            :is-creator="observations.isCreator.value"
          />
        </template>
      </STabs>
      <ChatroomPresence
        v-else
        :online-users="onlineUsers"
        :agents="agentList"
      />
    </div>

    <!-- Agents drawer: mobile only (tablet keeps the agents rail). -->
    <SDrawer
      v-if="isMobile"
      :open="agentsDrawerOpen"
      side="left"
      :title="t('conversation.chatroom.agents')"
      @close="agentsDrawerOpen = false"
    >
      <ChatroomAgentSidebar :agents="agentList" />
    </SDrawer>
    <!-- Presence drawer: mobile + tablet (presence rail only exists at lg+). -->
    <SDrawer
      v-if="!isDesktop"
      :open="peopleDrawerOpen"
      side="right"
      :title="t('conversation.chatroom.people')"
      @close="peopleDrawerOpen = false"
    >
      <STabs
        v-if="showRailTabs"
        v-model="railTab"
        :tabs="railTabs"
      >
        <template #tab-people>
          <ChatroomPresence
            :online-users="onlineUsers"
            :agents="agentList"
          />
        </template>
        <template #tab-observer>
          <ObserverPanel
            :observer-agents="observations.observerAgents.value"
            :observations="observations.observations.value"
            :loading="observations.observationsLoading.value"
            :has-more="observations.hasMore.value"
            :loading-more="observations.loadingMore.value"
            :agent-names="agentNames"
            @release="openRelease"
            @delete="onObservationDelete"
            @load-earlier="observations.loadEarlier"
          />
        </template>
        <template #tab-activity>
          <ActivityPanel
            :chatroom-id="chatroomId"
            :project-id="observerProjectId"
            :is-creator="observations.isCreator.value"
          />
        </template>
      </STabs>
      <ChatroomPresence
        v-else
        :online-users="onlineUsers"
        :agents="agentList"
      />
    </SDrawer>

    <ChatroomExportModal
      :open="exportOpen"
      :job="exportJob"
      @close="exportOpen = false"
      @submit="onExportSubmit"
    />

    <ObservationReleaseDialog
      ref="releaseDialogRef"
      :open="releaseTarget !== null"
      :observation="releaseTarget"
      :disclose="roomQuery.data.value?.disclose_observers ?? true"
      :normal-agents="releasableAgents"
      @close="releaseTarget = null"
      @submit="onReleaseSubmit"
    />
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useTemplateRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useQuery } from '@tanstack/vue-query'
import { useI18n } from 'vue-i18n'

import { useToast, useBreakpoint, useVisualViewport, useConfirmDialog, useResizablePanel } from '@shared/composables'
import { SAlert, SButton, SDrawer, SEmptyState, SResizeHandle, SSkeleton, STabs } from '@shared/ui'
import { ChatBubbleLeftRightIcon, EyeIcon, PlayCircleIcon, UsersIcon } from '@heroicons/vue/24/outline'
import { ApiError, ValidationError } from '@shared/errors'
import { isProblemWithType } from '@shared/transport'
import { useSessionStore } from '@shared/stores/session'
import { useOrchestrationStore } from '@shared/stores/orchestration'
import type { ApprovalWithVotes } from '@shared/types/workflow'
import { ApprovalCard } from '@slices/workflow'
import { ActivityPanel, getActiveActivation, useActivitiesStore } from '@slices/activities'

import { useChatroomSocket } from '../composables/useChatroomSocket'
import { useObservations } from '../composables/useObservations'
import { useChatroomMessages } from '../composables/useChatroomMessages'
import { useChatroomMessageEditing } from '../composables/useChatroomMessageEditing'
import { useChatroomAttachments } from '../composables/useChatroomAttachments'
import { useChatroomSearch } from '../composables/useChatroomSearch'
import { useChatroomExport } from '../composables/useChatroomExport'
import { useChatroomScroll } from '../composables/useChatroomScroll'
import { useAgentStreams } from '../composables/useAgentStreams'
import { useMarkdownEnhance } from '../composables/useMarkdownEnhance'
import { useConversationStore } from '../stores/conversation'
import { AGENT_ERROR_MESSAGE_KEYS, AGENT_ERROR_FALLBACK_KEY } from '../constants/agentErrors'
import { getChatroom, getWorkspace, listChatroomAgents, listChatroomMembers, listProjectAgents, type ExportOptions, type ReleaseBody } from '../api'
import { convKeys } from '../queries'
import type { AgentStatus } from '../components/ChatroomAgentStatusItem.vue'
import type { Message, Observation, SearchHit } from '../types'

import ChatroomHeader from '../components/ChatroomHeader.vue'
import ChatroomAgentSidebar from '../components/ChatroomAgentSidebar.vue'
import ChatroomMessageBubble from '../components/ChatroomMessageBubble.vue'
import ChatroomStreamingBubble from '../components/ChatroomStreamingBubble.vue'
import ChatroomPresence from '../components/ChatroomPresence.vue'
import ChatroomComposer from '../components/ChatroomComposer.vue'
import ChatroomTypingIndicator from '../components/ChatroomTypingIndicator.vue'
import ChatroomSearchPanel from '../components/ChatroomSearchPanel.vue'
import ChatroomExportModal from '../components/ChatroomExportModal.vue'
import ChatroomNewMessagesPill from '../components/ChatroomNewMessagesPill.vue'
import ChatroomLoadEarlier from '../components/ChatroomLoadEarlier.vue'
import ObserverPanel from '../components/ObserverPanel.vue'
import ObservationReleaseDialog from '../components/ObservationReleaseDialog.vue'

const { t } = useI18n()
const toast = useToast()
const { confirm } = useConfirmDialog()
const route = useRoute()
const router = useRouter()
const store = useConversationStore()
const session = useSessionStore()
const orchStore = useOrchestrationStore()
const activitiesStore = useActivitiesStore()
const chatroomId = route.params.chatroomId as string
const projectId = (route.params.projectId as string) || ''

const myId = computed(() => session.me?.id ?? null)
const { isMobile, isTablet, isDesktop } = useBreakpoint()
const { keyboardInset } = useVisualViewport(() => isMobile.value)

// Desktop right-rail width (R24.32). 200px was the historical fixed width, so it
// stays both the default and the floor: nothing regresses for a user who never
// drags.
//
// The ceiling is derived from the grid's own box, not the viewport: the app
// shell spends up to --sidebar-width (260px) before .chatroom sees any width,
// and collapsing that sidebar fires no window `resize`. Measuring the element
// is what makes "the message column always retains its minimum share" true
// rather than approximately true.
const RAIL_MIN_WIDTH = 200
// Keep in step with .chatroom's grid-template-columns below.
const AGENTS_RAIL_WIDTH = 220
const RAIL_HANDLE_WIDTH = 10
const MIN_FEED_WIDTH = 360
const chatroomRef = useTemplateRef<HTMLElement>('chatroomRef')
const {
  width: railWidth,
  maxWidth: railMaxWidth,
  setWidth: setRailWidth,
  reset: resetRailWidth,
} = useResizablePanel({
  storageKey: 'smap-chatroom-rail-w',
  defaultWidth: RAIL_MIN_WIDTH,
  min: RAIL_MIN_WIDTH,
  max: 720,
  container: chatroomRef,
  reserve: AGENTS_RAIL_WIDTH + RAIL_HANDLE_WIDTH + MIN_FEED_WIDTH,
})
store.setActive(chatroomId)

const listRef = useTemplateRef<HTMLElement>('listRef')

// ---- room + bound agents --------------------------------------------------
// Both queries degrade gracefully on purpose: a guest who can't read the room
// metadata (403) still gets a usable view — roomName falls back to the id and
// the agent list simply stays empty. Do not add error UI here.

const roomQuery = useQuery({
  queryKey: convKeys.chatroom(chatroomId),
  queryFn: () => getChatroom(chatroomId),
  retry: false,
})
const roomName = computed(() => roomQuery.data.value?.name ?? `#${chatroomId.slice(0, 8)}`)

const boundAgentsQuery = useQuery({
  queryKey: ['conversation', 'chatroom-agents', chatroomId],
  queryFn: () => listChatroomAgents(chatroomId),
  retry: false,
})

// Resolve workspace → project → agents to get agent display names. Each
// query gates on the previous via `enabled`, so missing room data does not
// trigger errors; the names map simply stays empty and falls back to the
// truncated id.
const workspaceQuery = useQuery({
  queryKey: computed(() => ['conversation', 'workspace', roomQuery.data.value?.workspace_id]),
  queryFn: () => getWorkspace(roomQuery.data.value!.workspace_id),
  enabled: computed(() => !!roomQuery.data.value?.workspace_id),
  retry: false,
})

const projectAgentsQuery = useQuery({
  queryKey: computed(() => ['conversation', 'project-agents', workspaceQuery.data.value?.project_id]),
  queryFn: () => listProjectAgents(workspaceQuery.data.value!.project_id),
  enabled: computed(() => !!workspaceQuery.data.value?.project_id),
  retry: false,
})

const agentNames = computed<Record<string, string>>(() => {
  const agents = projectAgentsQuery.data.value
  if (!agents) return {}
  const map: Record<string, string> = {}
  for (const a of agents) {
    map[a.id] = a.name
  }
  return map
})

// Human author display names (members + guests). One map resolves both REST
// history and live WS messages; absent/null names fall back to a truncated id.
// Degrades gracefully like the agent queries — a 403 just leaves the map empty.
const membersQuery = useQuery({
  queryKey: ['conversation', 'chatroom-members', chatroomId],
  queryFn: () => listChatroomMembers(chatroomId),
  retry: false,
})

const userNames = computed<Record<string, string>>(() => {
  const map: Record<string, string> = {}
  for (const m of membersQuery.data.value ?? []) {
    if (m.display_name) map[m.user_id] = m.display_name
  }
  return map
})

function agentStatus(id: string): AgentStatus {
  if (store.agentStreams[chatroomId]?.[id]) return 'streaming'
  if (store.agentThinking[chatroomId]?.has(id)) return 'thinking'
  if (store.agentErrors[chatroomId]?.[id]) return 'error'
  return 'idle'
}

// Observers are excluded from every shared surface (R28.10, locked decision
// 7): the sidebar/presence rail, mention autocomplete, and the streaming
// bubbles all derive from `agentList`, so this single filter keeps the shared
// UI identical on the creator's screen and a member's screen (matters for
// screen-sharing). Observers live only in the creator-only Observer tab.
const agentList = computed(() =>
  (boundAgentsQuery.data.value ?? [])
    .filter((a) => a.role !== 'observer')
    .map((a) => {
      const errorReason = store.agentErrors[chatroomId]?.[a.agent_id]
      return {
        id: a.agent_id,
        name: agentNames.value[a.agent_id] ?? a.agent_id.slice(0, 8),
        status: agentStatus(a.agent_id),
        ...(errorReason !== undefined && { errorReason }),
      }
    }),
)

// Autocomplete-only mention source: bound agents plus named human members.
// The send path resolves wake targets by name against `agentList` only, so a
// user whose name collides with a bound agent would summon that agent if
// suggested — exclude those collisions here so picking a person from the list
// only ever inserts plain text and never wakes an agent. Unnamed users are
// omitted since they have no handle to match.
const mentionables = computed<{ id: string; name: string }[]>(() => {
  const agentNameSet = new Set(agentList.value.map((a) => a.name.toLowerCase()))
  const users = (membersQuery.data.value ?? [])
    .filter((m): m is { user_id: string; display_name: string } => !!m.display_name)
    .filter((m) => !agentNameSet.has(m.display_name.toLowerCase()))
    .map((m) => ({ id: m.user_id, name: m.display_name }))
  return [...agentList.value, ...users]
})

// ---- observer agents (creator-only, R28.03) -------------------------------

const observerProjectId = computed(
  () => workspaceQuery.data.value?.project_id || projectId || undefined,
)
const observations = useObservations(chatroomId, {
  room: computed(() => roomQuery.data.value),
  projectId: observerProjectId,
  boundAgents: computed(() => boundAgentsQuery.data.value),
  agentNames,
})

// Right rail switches to a tabbed People/Observer view for the creator once
// there is a surface to show — either a bound observer or observations that
// outlived one (they remain readable/releasable/deletable regardless of the
// current roster). Everyone else keeps the plain presence panel (and never
// learns an observer exists beyond the neutral disclosure chip).
const showObserverTab = computed(() => observations.hasObserverSurface.value)
const showActivityTab = computed(
  () => observations.isCreator.value || !!activitiesStore.getActivation(chatroomId),
)
const showRailTabs = computed(() => showObserverTab.value || showActivityTab.value)
// Declared here (ahead of its drawer markup) so the W-3 visibility computed
// below can read it without a temporal-dead-zone hit under the immediate watch.
const peopleDrawerOpen = ref(false)
const railTab = ref<'people' | 'observer' | 'activity'>('people')
const railTabs = computed(() => [
  { key: 'people', label: t('conversation.chatroom.people'), icon: UsersIcon },
  ...(showObserverTab.value
    ? [{
        key: 'observer',
        label: t('conversation.observers.tab'),
        icon: EyeIcon,
        ariaLabel: t('conversation.observers.badgeAria', { n: observations.unreadCount.value }),
        badgeLive: true,
        ...(observations.unreadCount.value && { badge: observations.unreadCount.value }),
      }]
    : []),
  ...(showActivityTab.value
    ? [{ key: 'activity', label: t('activities.panel.tab'), icon: PlayCircleIcon }]
    : []),
])
// The Observer tab can now disappear from a live session — not just on a fresh
// mount — because its visibility tracks mutable observation data (§7 of
// observation-binding-cleanup): deleting the last stranded observation while
// no observer is bound flips `hasObserverSurface` false with the tab still
// selected. Left alone, `railTab` would point at a key STabs no longer renders
// a panel for (empty rail). Fall back to the first tab that still exists.
watch(railTabs, (tabs) => {
  // 'people' is always the first, unconditional entry — the one tab that can
  // never disappear out from under the current selection.
  if (!tabs.some((tab) => tab.key === railTab.value)) {
    railTab.value = 'people'
  }
})
// W-3 (B.3/B.8): the panel is only actually visible when the Observer tab is
// selected AND its container is on screen — the desktop rail is always shown,
// but on mobile/tablet the tab lives inside the people drawer. Tracking railTab
// alone left `panelOpen` stuck true after the drawer closed, so the unread
// badge stopped counting.
const observerPanelVisible = computed(
  () => railTab.value === 'observer' && (isDesktop.value || peopleDrawerOpen.value),
)
watch(observerPanelVisible, (visible) => observations.setPanelOpen(visible), { immediate: true })

// Normal-role bound agents (names resolved) offered as private-release targets.
const releasableAgents = computed(() =>
  (boundAgentsQuery.data.value ?? [])
    .filter((a) => a.role !== 'observer')
    .map((a) => ({ id: a.agent_id, name: agentNames.value[a.agent_id] ?? a.agent_id.slice(0, 8) })),
)

const releaseTarget = ref<Observation | null>(null)
const releaseDialogRef = ref<InstanceType<typeof ObservationReleaseDialog> | null>(null)

function openRelease(o: Observation): void {
  releaseTarget.value = o
}

async function onReleaseSubmit(body: ReleaseBody): Promise<void> {
  const target = releaseTarget.value
  if (!target) return
  releaseDialogRef.value?.setSubmitting(true)
  try {
    await observations.release(target.id, body)
    releaseTarget.value = null
    toast.success(t('conversation.observers.releaseSuccess'))
  } catch (err) {
    // W-6 (F-10): the transport throws typed ApiError/ValidationError, never
    // an AxiosError — branch on those, not on err.response.
    if (err instanceof ApiError && err.status === 409) {
      // Already released by a concurrent action — refetch and dismiss.
      await observations.refetch()
      releaseTarget.value = null
      toast.info(t('conversation.observers.alreadyReleased'))
      return
    }
    if (err instanceof ValidationError && isProblemWithType(err, '/invalid-release-target')) {
      // Targets became ineligible (e.g. an agent unbound/role-flipped since
      // the dialog opened) — surface the server's specific detail inline.
      releaseDialogRef.value?.setError(err.detail ?? t('conversation.observers.releaseFailed'))
      return
    }
    releaseDialogRef.value?.setError(t('conversation.observers.releaseFailed'))
  } finally {
    releaseDialogRef.value?.setSubmitting(false)
  }
}

async function onObservationDelete(o: Observation): Promise<void> {
  const ok = await confirm({
    title: t('conversation.observers.deleteTitle'),
    message: t('conversation.observers.deleteConfirm'),
    variant: 'warning',
  })
  if (!ok) return
  try {
    await observations.remove(o.id)
  } catch {
    toast.error(t('conversation.observers.deleteFailed'))
  }
}

// ---- composables ----------------------------------------------------------

const {
  messages,
  hasOlderMessages,
  loadingOlder,
  loadEarlier,
  isPending: messagesPending,
  isError: messagesErrored,
  refetchMessages,
  rendered,
  draft,
  onSend,
  confirmDelete,
  downloadAttachment,
  canEdit,
  canDelete,
  dropOlderMessage,
  refreshOlderMessage,
} = useChatroomMessages(
  chatroomId,
  // Report the send; useChatroomScroll owns what happens to the feed's scroll
  // position, so it is the one that resets the pill and the at-bottom flag.
  () => scrollToBottom(),
  () => agentList.value,
  () => roomQuery.data.value?.is_moderator ?? false,
)

// The member roster is fetched once, but new authors (and renames) appear over
// the room's lifetime via WebSocket. When a user message arrives from a sender
// the roster doesn't name yet, refetch it once for that id so the author label
// resolves instead of staying a truncated id. Tracking attempted ids bounds
// this to one refetch per sender (a sender with no display name stays unnamed
// without re-querying every message).
const resolvedSenderAttempts = new Set<string>()
watch(messages, (list) => {
  let needsRefetch = false
  for (const m of list) {
    if (
      m.sender_type === 'user' &&
      m.sender_id &&
      !(m.sender_id in userNames.value) &&
      !resolvedSenderAttempts.has(m.sender_id)
    ) {
      resolvedSenderAttempts.add(m.sender_id)
      needsRefetch = true
    }
  }
  if (needsRefetch) void membersQuery.refetch()
})

const { editingId, editDraft, startEdit, cancelEdit, saveEdit } =
  useChatroomMessageEditing(chatroomId)

const {
  pendingUploads,
  uploadFiles,
  onDrop,
  removeUpload,
  attachmentIds,
  clear: clearAttachments,
} = useChatroomAttachments(
  chatroomId,
  () => workspaceQuery.data.value?.project_id || projectId || undefined,
)

const { streamingEntries } = useAgentStreams(chatroomId)

const { searchQuery, searchHits, renderedSnippets, runSearch } = useChatroomSearch(chatroomId)
const { exportJob, runExport, reset: resetExport } = useChatroomExport(chatroomId)
const liveApprovals = computed(() => orchStore.getApprovalsForRoom(chatroomId))

// One ordered feed. 07-conversation.md:988 places an approval card "at the
// chronological position where the approval was requested, interleaved with
// regular messages"; rendered as a second list after the messages, its position
// was the store's insertion order instead (F-47).
//
// Approvals are merged INTO the message order rather than the two being sorted
// together: message order comes from the server and must survive intact, and an
// optimistic send carries a client clock that a joint sort could rank ahead of
// an older persisted row. Only the approvals' placement is computed here.
//
// The ordering is deliberately not pushed into `getApprovalsForRoom`
// (shared/stores/orchestration.ts): that store has non-chatroom consumers, and
// a chatroom-specific order imposed on `shared` is a layer violation in the one
// direction that is never allowed.
type FeedItem =
  | { kind: 'message'; key: string; message: (typeof messages.value)[number] }
  | { kind: 'approval'; key: string; approval: ApprovalWithVotes }

const feedItems = computed<FeedItem[]>(() => {
  // Approvals carry `started_at`, messages `created_at`, so the merge reads a
  // per-kind key. Parsed rather than string-compared: the two sources differ in
  // sub-second precision, and `...:00Z` sorts before `...:00.000Z` lexically
  // despite naming the same instant.
  const pending = liveApprovals.value
    .map((a) => ({ approval: a, at: Date.parse(a.started_at) }))
    .sort((x, y) => x.at - y.at)
  const out: FeedItem[] = []
  let next = 0
  const drainThrough = (limit: number): void => {
    // NaN from an unparseable timestamp fails this test and falls to the tail,
    // which shows the card rather than dropping it.
    while (next < pending.length && pending[next]!.at <= limit) {
      const { approval } = pending[next]!
      out.push({ kind: 'approval', key: `approval-${approval.id}`, approval })
      next++
    }
  }
  for (const m of messages.value) {
    drainThrough(Date.parse(m.created_at))
    out.push({ kind: 'message', key: m.id, message: m })
  }
  drainThrough(Number.POSITIVE_INFINITY)
  return out
})

// What the unseen counter watches. Ids, not a length: only identity can tell an
// arrival from a prepend (F-12). Approvals are in it, so one arriving while the
// reader is scrolled up raises the pill like any other item.
const feedIds = computed(() => feedItems.value.map((i) => i.key))

const {
  showPill,
  newCount,
  highlightId,
  scrollToBottom,
  scrollToMessage,
  maybeStick,
  captureBeforePrepend,
  restoreAfterPrepend,
  observeTop,
} = useChatroomScroll(listRef, feedIds)

// Debounced KaTeX/Mermaid post-processing; re-pin scroll after each update.
useMarkdownEnhance(listRef, { onAfterUpdate: maybeStick })

/** Send the draft + resolved attachments, clearing uploads on success. */
async function send(): Promise<void> {
  // Bound as an event handler, so nothing awaits this promise: a branch of
  // onSend that rejects instead of reporting its outcome would surface only as
  // an unhandled rejection (F-9). Every branch reports today — the catch is the
  // structural guarantee that the next one added has to as well.
  let ok = false
  try {
    ok = await onSend(attachmentIds())
  } catch {
    toast.error(t('conversation.chatroom.sendFailed'))
  }
  if (ok) clearAttachments()
}

// ---- WebSocket + real-time state ------------------------------------------

let typingTimer: ReturnType<typeof setTimeout> | null = null
const TYPING_DEBOUNCE_MS = 3000

// NB: message sending is REST (sendMessage), independent of this socket, so the
// composer is intentionally NOT gated on `connected` — a flapping/degraded WS
// must not lock the user out of sending. The pill shows `connectionState`.
const { connectionState, channel: wsChannel } = useChatroomSocket(chatroomId)

wsChannel.subscribe('message.updated', (ev) => void refreshOlderMessage(ev.message_id as string))
wsChannel.subscribe('message.deleted', (ev) => dropOlderMessage(ev.message_id as string))

function emitTyping(): void {
  if (typingTimer === null) {
    wsChannel.send({ type: 'typing.start' })
  } else {
    clearTimeout(typingTimer)
  }
  typingTimer = setTimeout(() => {
    wsChannel.send({ type: 'typing.stop' })
    typingTimer = null
  }, TYPING_DEBOUNCE_MS)
}

function onKeyDown(e: KeyboardEvent): void {
  if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
    const tag = (document.activeElement as HTMLElement | null)?.tagName
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
    e.preventDefault()
    searchOpen.value = !searchOpen.value
  }
}

let isUnmounted = false

async function hydrateActivityActivation(): Promise<void> {
  try {
    const activation = await getActiveActivation(chatroomId)
    if (isUnmounted) return
    if (activitiesStore.getActivation(chatroomId) !== undefined) return
    if (activation) activitiesStore.setActivation(chatroomId, activation)
    else activitiesStore.clearActivation(chatroomId)
  } catch {
    if (!isUnmounted) toast.error(t('activities.panel.loadFailed'))
  }
}

onMounted(() => {
  document.addEventListener('keydown', onKeyDown)
  void hydrateActivityActivation()
})

onBeforeUnmount(() => {
  isUnmounted = true
  document.removeEventListener('keydown', onKeyDown)
  if (typingTimer !== null) {
    clearTimeout(typingTimer)
    typingTimer = null
  }
})

const typingNames = computed(() => {
  const set = store.typingUsers[chatroomId]
  if (!set) return []
  return Array.from(set)
    .filter((uid) => uid !== myId.value)
    .map((uid) => uid.slice(0, 8))
})

const onlineUsers = computed(() => {
  const set = store.presence[chatroomId]
  if (!set) return []
  return Array.from(set).map((id) => ({ id, isYou: id === myId.value }))
})

// Agent failure surfaced by the socket layer: backend agent.finished{error}
// or the client-side thinking watchdog ('timeout'). Known skip reasons get a
// specific message; anything else falls back to the generic failure. Toast
// once, then clear.
watch(
  () => store.agentError[chatroomId],
  (err) => {
    if (!err) return
    toast.error(t(AGENT_ERROR_MESSAGE_KEYS[err] ?? AGENT_ERROR_FALLBACK_KEY))
    store.setAgentError(chatroomId, null)
  },
)

// ---- header actions -------------------------------------------------------

const searchOpen = ref(false)
const searching = ref(false)
const exportOpen = ref(false)
const agentsDrawerOpen = ref(false)

function goBack(): void {
  router.back()
}

function goSettings(): void {
  void router.push({ name: 'conversation.chatroom.settings', params: { chatroomId } })
}

function senderName(m: Message): string {
  if (m.sender_type === 'agent' && m.sender_id) {
    return agentNames.value[m.sender_id] ?? m.sender_id.slice(0, 8)
  }
  if (m.sender_type === 'user' && m.sender_id) {
    return userNames.value[m.sender_id] ?? m.sender_id.slice(0, 8)
  }
  return m.sender_id ? m.sender_id.slice(0, 8) : m.sender_type
}

async function copyMessage(m: Message): Promise<void> {
  if (!navigator.clipboard) {
    toast.error(t('conversation.chatroom.copyFailed'))
    return
  }
  try {
    await navigator.clipboard.writeText(m.content_md)
    toast.success(t('conversation.chatroom.copied'))
  } catch {
    toast.error(t('conversation.chatroom.copyFailed'))
  }
}

async function doSearch(): Promise<void> {
  searching.value = true
  try {
    await runSearch()
  } finally {
    searching.value = false
  }
}

function onSelectHit(hit: SearchHit): void {
  searchOpen.value = false
  // The panel sits over the feed; let it unmount before scrolling so the
  // target message is not obscured. A hit may reference a message that has
  // not been paginated into the feed yet — there is no "load-around" endpoint,
  // so we tell the user rather than scrolling to nothing.
  void nextTick(() => {
    if (!scrollToMessage(hit.message_id)) {
      toast.info(t('conversation.chatroom.searchJumpUnavailable'))
    }
  })
}

async function onLoadEarlier(): Promise<void> {
  captureBeforePrepend()
  await loadEarlier()
  restoreAfterPrepend()
}

// Scroll-based pagination (07-conversation.md:895). The button stays as the
// fallback the same line calls for; this is the half that was never built.
//
// Re-armed on every appearance of the row, because it is `v-if`-ed out while
// the first page is pending and for good once `hasOlderMessages` goes false --
// which is also what terminates the loop.
const loadEarlierRef = useTemplateRef<HTMLElement>('loadEarlierRef')
let disposeTopObserver: (() => void) | null = null

watch(
  loadEarlierRef,
  (el) => {
    disposeTopObserver?.()
    disposeTopObserver = null
    if (!el) return
    disposeTopObserver = observeTop(el, () => {
      // `loadingOlder` is the re-entrancy guard: reaching the threshold again
      // while a page is still in flight must not start a second one.
      if (hasOlderMessages.value && !loadingOlder.value) void onLoadEarlier()
    })
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  disposeTopObserver?.()
  disposeTopObserver = null
})

function openExport(): void {
  // Cancel any earlier job's poller as well as clearing the slot, so its
  // completion cannot render into the fresh configuration form (F-16).
  resetExport()
  exportOpen.value = true
}

function onExportSubmit(opts: ExportOptions): void {
  void runExport(opts)
}
</script>

<style scoped>
.chatroom {
  display: grid;
  /* Track 3 is the resize handle; the rail track reads its width from the
     custom property the view binds, falling back to the historical 200px when
     no width has been chosen (or when storage is unavailable). The 220px and
     10px literals are mirrored by AGENTS_RAIL_WIDTH / RAIL_HANDLE_WIDTH in the
     script, which is where the rail's ceiling is derived from. */
  grid-template-columns: 220px 1fr 10px var(--chatroom-rail-w, 200px);
  grid-template-rows: 48px 1fr auto auto;
  height: 100%;
  overflow: hidden;
}

.chatroom__header {
  grid-column: 1 / -1;
  grid-row: 1;
}

.chatroom__agents {
  grid-column: 1;
  grid-row: 2 / -1;
}

.chatroom__feed {
  grid-column: 2;
  grid-row: 2;
  position: relative;
  min-height: 0;
  overflow: hidden;
}

.messages {
  height: 100%;
  overflow-y: auto;
  list-style: none;
  margin: 0;
  padding: 16px;
  /* A flex column purely so the empty state below has a track to grow into.
     Ordinary items are block-level and size to content either way, and no item
     sets `flex`, so nothing else changes. */
  display: flex;
  flex-direction: column;
}

/* 07-conversation.md:1018 centres the empty state in the feed area. The height
   has to come from here: the item sizes to content, so SEmptyState centring
   itself would have nothing to distribute. Written so that a self-centring
   SEmptyState (the sibling dossier's F-30) still centres inside it. */
.chatroom__empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* Real-time list animations (§7.2 / §7.5): new messages slide in, deleted ones
   fade out. Initial-render items are NOT animated (no `appear`), so opening a
   busy room does not flush the whole backlog through the transition. */
.msg-enter-active {
  transition:
    opacity 200ms ease,
    transform 200ms ease;
}

.msg-leave-active {
  transition:
    opacity 150ms ease,
    transform 150ms ease;
}

.msg-enter-from {
  opacity: 0;
  transform: translateY(8px);
}

.msg-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

/* On send success the optimistic placeholder (a `pending-<uuid>` vnode key) is
   replaced by its persisted twin (the real id) at the same position — a key
   swap Vue animates as leave+enter. Drop the placeholder's leave transition so
   that swap reads as a single clean settle, not a cross-fade flash. */
.bubble-row--pending.msg-leave-active {
  transition: none;
}

@media (prefers-reduced-motion: reduce) {
  .msg-enter-active,
  .msg-leave-active {
    transition: none;
  }
}

.chatroom__typing {
  grid-column: 2;
  grid-row: 3;
}

.chatroom__composer {
  grid-column: 2;
  grid-row: 4;
}

.chatroom__rail-handle {
  grid-column: 3;
  grid-row: 2 / -1;
}

.chatroom__presence {
  grid-column: 4;
  grid-row: 2 / -1;
  /* The same contract .chatroom__feed already has. A grid item defaults to
     `min-height: auto`, which refuses to shrink below its content: tall rail
     content therefore overflowed the grid and was clipped by .chatroom's
     `overflow: hidden`, with no scrollbar anywhere in the ancestry to reach it. */
  min-height: 0;
  overflow: hidden;
}

.chatroom__pill {
  position: absolute;
  bottom: 16px;
  left: 50%;
  transform: translateX(-50%);
}

/* Tablet (768-1023): 2-column — agents rail + feed. Presence is a drawer. */
.chatroom--tablet {
  grid-template-columns: 200px 1fr;
}

/* Mobile: single column; side panels become drawers. The grid shrinks by the
   keyboard overlap so the composer stays visible above the virtual keyboard. */
.chatroom--mobile {
  grid-template-columns: 1fr;
  grid-template-rows: 48px 1fr auto auto;
  height: calc(100% - var(--kb-inset, 0px));
}

.chatroom--mobile .chatroom__feed,
.chatroom--mobile .chatroom__typing,
.chatroom--mobile .chatroom__composer {
  grid-column: 1;
}
</style>
