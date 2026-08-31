<template>
  <section
    ref="chatroomRef"
    class="chatroom"
    :class="{
      'chatroom--mobile': isMobile,
      'chatroom--tablet': isTablet,
      'chatroom--compact': isCompactDesktop,
    }"
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
      :is-compact="isCompactDesktop"
      :agents-open="agentsDrawerOpen"
      :people-open="peopleDrawerOpen"
      :observers-present="roomQuery.data.value?.observers_present ?? false"
      :can-export="!(roomQuery.data.value?.viewer_is_guest ?? false)"
      @back="goBack"
      @search="surfaces.open('search')"
      @settings="goSettings"
      @export="openExport"
      @toggle-agents="surfaces.toggle('agents')"
      @toggle-people="surfaces.toggle('people')"
    />

    <!-- One scrim for all three transient surfaces, because only one of them
         can be active. Placed in the grid rather than inset over it so its area
         follows the band: the feed column alone for search, everything below
         the header once a rail overlay covers the composer too. -->
    <div
      v-if="scrimSurface"
      class="chatroom__scrim"
      :class="`chatroom__scrim--${scrimSurface}`"
      role="none"
      @click="surfaces.close()"
      @keydown.enter="surfaces.close()"
    />

    <ChatroomAgentSidebar
      v-if="!isMobile"
      ref="agentsPanelEl"
      class="chatroom__agents"
      :class="{ 'chatroom__panel--open': agentsDrawerOpen }"
      :agents="agentList"
      :aria-label="t('conversation.chatroom.agents')"
      tabindex="-1"
    />

    <div class="chatroom__feed">
      <Transition name="search-panel">
        <ChatroomSearchPanel
          v-if="searchOpen"
          :query="searchQuery"
          :hits="searchHits"
          :rendered-snippets="renderedSnippets"
          :searching="searching"
          @update:query="searchQuery = $event"
          @search="doSearch"
          @close="surfaces.close('search')"
          @select="onSelectHit"
        />
      </Transition>

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

    <!-- One grid cell (row 3) holding both notices, rather than a new row for the
         chip: the grid's rows are numbered explicitly below, so inserting one
         would renumber the composer and every breakpoint override of it. AC-11
         wants the chip directly above the composer, which is where this row
         already is. -->
    <div class="chatroom__typing">
      <ChatroomTypingIndicator :names="typingNames" />
      <SDraftDisclosureChip v-if="draftsReadable" />
    </div>

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
    <!-- No handle in the compact band: an overlay panel has no track to size. -->
    <SResizeHandle
      v-if="isDesktop && !isCompactDesktop"
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
      ref="peoplePanelEl"
      class="chatroom__presence"
      :class="{ 'chatroom__panel--open': peopleDrawerOpen }"
      role="complementary"
      :aria-label="t('conversation.chatroom.people')"
      tabindex="-1"
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
            :drafts-readable="draftsReadable"
            @draft="(key, payload) => drafts.reportActivity(key, payload)"
            @draft-clear="(key) => drafts.clearActivity(key)"
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
      @close="surfaces.close('agents')"
    >
      <ChatroomAgentSidebar :agents="agentList" />
    </SDrawer>
    <!-- Presence drawer: mobile + tablet (presence rail only exists at lg+). -->
    <SDrawer
      v-if="!isDesktop"
      :open="peopleDrawerOpen"
      side="right"
      :title="t('conversation.chatroom.people')"
      @close="surfaces.close('people')"
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
            :drafts-readable="draftsReadable"
            @draft="(key, payload) => drafts.reportActivity(key, payload)"
            @draft-clear="(key) => drafts.clearActivity(key)"
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
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useTemplateRef, watch, type ComponentPublicInstance, type Ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useQuery } from '@tanstack/vue-query'
import { useI18n } from 'vue-i18n'

import { useToast, useBreakpoint, useVisualViewport, useConfirmDialog, useFocusTrap, useResizablePanel, BP } from '@shared/composables'
import {
  SAlert,
  SButton,
  SDraftDisclosureChip,
  SDrawer,
  SEmptyState,
  SResizeHandle,
  SSkeleton,
  STabs,
} from '@shared/ui'
import { ChatBubbleLeftRightIcon, EyeIcon, PlayCircleIcon, UsersIcon } from '@heroicons/vue/24/outline'
import { ApiError, ValidationError } from '@shared/errors'
import { isProblemWithType } from '@shared/transport'
import { useSessionStore } from '@shared/stores/session'
import { useOrchestrationStore } from '@shared/stores/orchestration'
import type { ApprovalWithVotes } from '@shared/types/workflow'
import { ApprovalCard } from '@slices/workflow'
import { ActivityPanel, getActiveActivation, useActivitiesStore } from '@slices/activities'

import { useChatroomSocket } from '../composables/useChatroomSocket'
import { useDraftReporting } from '../composables/useDraftReporting'
import { useObservations } from '../composables/useObservations'
import { useChatroomMessages } from '../composables/useChatroomMessages'
import { useChatroomMessageEditing } from '../composables/useChatroomMessageEditing'
import { useChatroomAttachments } from '../composables/useChatroomAttachments'
import { useChatroomSearch } from '../composables/useChatroomSearch'
import { useChatroomExport } from '../composables/useChatroomExport'
import { useChatroomScroll } from '../composables/useChatroomScroll'
import { useAgentStreams } from '../composables/useAgentStreams'
import { useMarkdownEnhance } from '../composables/useMarkdownEnhance'
import { useTransientSurfaces } from '../composables/useTransientSurfaces'
import { useConversationStore } from '../stores/conversation'
import { agentErrorMessageKey } from '../constants/agentErrors'
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
const { width: viewportWidth, isMobile, isTablet, isDesktop } = useBreakpoint()
const { keyboardInset } = useVisualViewport(() => isMobile.value)

// The fourth band 07-conversation.md:238 needs and useBreakpoint does not
// expose. Derived locally from the exported width rather than widening the
// shared composable for one consumer; if a second view needs it, promote it
// there instead of copying this (FU-2).
const isCompactDesktop = computed(() => viewportWidth.value >= BP.lg && viewportWidth.value < BP.xl)

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
// [R32.05]. The server has already folded the room's `disclose_drafts` into this,
// so the client never has to combine two flags and cannot get the combination
// wrong. Defaults to false while the room read is in flight: a chip that flashes
// on and off would be worse than one that arrives a moment late.
const draftsReadable = computed(() => roomQuery.data.value?.drafts_readable ?? false)

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
// Search, agents and people/observer are one mutually exclusive group wherever
// more than one of them is transient (Q-5/Q-8). The coordinator holds that
// single owner plus the focus-restoration target; the three booleans below are
// projections of it, kept under their old names because the drawers, the header
// and the panel classes all read them.
//
// Declared here (ahead of its drawer markup) so the W-3 visibility computed
// below can read `peopleDrawerOpen` without a temporal-dead-zone hit under the
// immediate watch.
const surfaces = useTransientSurfaces()
const searchOpen = computed(() => surfaces.isOpen('search'))
const agentsDrawerOpen = computed(() => surfaces.isOpen('agents'))
const peopleDrawerOpen = computed(() => surfaces.isOpen('people'))
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
  // An unparseable `started_at` is mapped to +Infinity, NOT left as NaN. NaN
  // fails EVERY `<=` test including the final drain, so a single bad timestamp
  // would strand its own card and every approval sorted after it -- silently
  // dropping a gate the user has to vote on. `orchestration.ts:65` already
  // guards the same field with `Number.isFinite`, so the case is reachable.
  // Infinity sorts such a card to the tail, where it is at least visible.
  const pending = liveApprovals.value
    .map((a) => {
      const at = Date.parse(a.started_at)
      return { approval: a, at: Number.isFinite(at) ? at : Number.POSITIVE_INFINITY }
    })
    .sort((x, y) => x.at - y.at)
  const out: FeedItem[] = []
  let next = 0
  const drainThrough = (limit: number): void => {
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
  if (ok) {
    clearAttachments()
    // AC-4. Only on success: a failed send leaves the text in the composer, and
    // it is still unsent, so retracting it would hide a live draft. `onSend`
    // itself clears `draft`, so the client-side state and the server-side entry
    // are retired together.
    drafts.clearComposer()
  }
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

// §32. The view owns the socket on the composer's and the activity panel's
// behalf — the activities slice must never reach it (gate #1's SLICE_DEPS), and
// `ChatroomComposer` already delegates its typing signal here.
const drafts = useDraftReporting((frame) => wsChannel.send(frame))

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
  // Rides the existing burst timer rather than adding a second one (AC-3): the
  // composer already calls this once per burst, so a draft costs the room the
  // same frame rate the typing indicator always has.
  drafts.reportComposer(draft.value)
}

function onKeyDown(e: KeyboardEvent): void {
  if (!(e.ctrlKey || e.metaKey) || e.key !== 'k') return
  // A stacked modal owns the keyboard; toggling a surface underneath it is not
  // something the user can see happen. Same test as onWindowKeydown below.
  if (document.querySelector('[aria-modal="true"]')) return
  // Opening search now moves focus into its field, so the text-entry guard
  // below would otherwise swallow the very keystroke that closes it again and
  // leave the shortcut one-way.
  if (!searchOpen.value) {
    const tag = (document.activeElement as HTMLElement | null)?.tagName
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
  }
  e.preventDefault()
  surfaces.toggle('search')
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
    toast.error(t(agentErrorMessageKey(err)))
    store.setAgentError(chatroomId, null)
  },
)

// ---- header actions -------------------------------------------------------

const searching = ref(false)
const exportOpen = ref(false)

// Panel visibility does not survive a change of layout band: a panel left open
// in one band would otherwise reappear as an already-open drawer in whichever
// band the viewport lands in next. `reset` rather than `close` because the
// control that opened it may have been unmounted by the same resize, and
// focusing a detached node drops focus to <body>.
watch([isMobile, isCompactDesktop], () => surfaces.reset())

// Which surface the scrim is currently backing, and how far it reaches. Search
// dims only the feed it is scoped to (07-conversation.md:750); a compact rail
// overlay covers the composer as well, so its scrim has to reach the same
// content the panel does or the composer stays clickable behind a dimmed feed.
// At >=1280 the rails are persistent and never scrimmed.
const scrimSurface = computed<'search' | 'rail' | null>(() => {
  if (searchOpen.value) return 'search'
  if (isCompactDesktop.value && (agentsDrawerOpen.value || peopleDrawerOpen.value)) return 'rail'
  return null
})

// The two in-chat rail panels get the drawer's focus behaviour without the
// drawer's modality: focus moves in on open and Tab stays inside, but the
// document keeps scrolling (they cover only part of it) and restoration is the
// coordinator's, which is the only thing that can tell a close from a hand-off.
// Only in the compact band — below lg the same content is an SDrawer with its
// own trap, and at >=1280 these are persistent rails that must stay in the
// normal tab order.
const agentsPanelEl = useTemplateRef<ComponentPublicInstance>('agentsPanelEl')
const agentsPanelRef = computed(() => (agentsPanelEl.value?.$el as HTMLElement | null) ?? null)
const peoplePanelRef = useTemplateRef<HTMLElement>('peoplePanelEl')
const railTrapOptions = { lockScroll: false, restoreFocus: false } as const
const { trapTab: trapAgentsTab } = useFocusTrap(
  agentsPanelRef,
  () => isCompactDesktop.value && agentsDrawerOpen.value,
  railTrapOptions,
)
const { trapTab: trapPeopleTab } = useFocusTrap(
  peoplePanelRef,
  () => isCompactDesktop.value && peopleDrawerOpen.value,
  railTrapOptions,
)

// Tab containment for the compact overlay panels, bound on the panels rather
// than on window: at >=1280 the same elements are persistent rails, where
// trapping Tab would lock a keyboard user inside a column they never asked to
// enter. Escape is handled at window level below instead, because a surface can
// be dismissed from outside itself — search leaves the composer reachable.
//
// Attached imperatively rather than with `@keydown`, because gate #11's
// `no-static-element-interactions` rejects a key handler on a non-interactive
// element and a panel is exactly that: a container, not a control.
function bindTabTrap(
  el: Readonly<Ref<HTMLElement | null>>,
  trap: (e: KeyboardEvent) => void,
): void {
  const handler = (e: KeyboardEvent): void => {
    if (isCompactDesktop.value && e.key === 'Tab') trap(e)
  }
  watch(
    el,
    (next, prev) => {
      prev?.removeEventListener('keydown', handler)
      next?.addEventListener('keydown', handler)
    },
    { immediate: true },
  )
  onBeforeUnmount(() => el.value?.removeEventListener('keydown', handler))
}

bindTabTrap(agentsPanelRef, trapAgentsTab)
bindTabTrap(peoplePanelRef, trapPeopleTab)

// Escape dismisses whichever surface is active, restoring focus to the control
// that opened it.
function onWindowKeydown(e: KeyboardEvent): void {
  if (e.key !== 'Escape') return
  // Every true modal in this view -- the export dialog, the observation release
  // dialog, any SConfirmDialog -- teleports an `aria-modal` panel to <body> and
  // handles Escape itself, and that keypress still bubbles to window. Asking the
  // document whether one is open rather than naming them keeps this correct when
  // the next dialog is added; naming only `exportOpen` let Escape on the release
  // dialog also shut the observer rail it was opened from. An SDrawer matches
  // too, which is right: it emits its own close through the coordinator, so the
  // surface is already dismissed by the time this would have run.
  if (document.querySelector('[aria-modal="true"]')) return
  if (surfaces.active.value === null) return
  surfaces.close()
}

onMounted(() => window.addEventListener('keydown', onWindowKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onWindowKeydown))

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
  surfaces.close('search')
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
  const before = messages.value.length
  captureBeforePrepend()
  try {
    await loadEarlier()
    // Proof the trigger is not exhausted after all: a click that adds history
    // is the way back from a latch caused by a transient failure.
    if (messages.value.length > before) autoLoadExhausted = false
  } finally {
    // Unconditional: the capture disarms the arrival watch and the top trigger
    // until the restore rearms them, so a throw between the two would leave the
    // feed without a pill and without auto-pagination for the rest of the
    // session. `loadEarlierPage` swallows its own failures, but its retry arm
    // awaits `invalidateQueries` from inside a catch, which can reject.
    restoreAfterPrepend()
  }
}

// Scroll-based pagination (07-conversation.md:895). The button stays as the
// fallback the same line calls for; this is the half that was never built.
//
// Re-armed on every appearance of the row, because it is `v-if`-ed out while
// the first page is pending and for good once `hasOlderMessages` goes false --
// which is also what terminates the loop.
const loadEarlierRef = useTemplateRef<HTMLElement>('loadEarlierRef')
let disposeTopObserver: (() => void) | null = null

// The auto-trigger retires itself once a load stops making progress. A page that
// comes back entirely duplicates leaves the feed, the cursor and
// `hasOlderMessages` all unchanged, so the sentinel keeps intersecting and the
// same request would be reissued forever -- a loop a human clicking the button
// cannot produce.
//
// It latches on a swallowed failure too, because `loadEarlierPage` toasts most
// errors without throwing and an errored page is indistinguishable from an
// empty one here. That would otherwise disable scroll pagination for the whole
// session after one network blip, so `onLoadEarlier` un-latches it on any load
// that genuinely adds history -- which a click on the still-live button can do.
let autoLoadExhausted = false

async function autoLoadEarlier(): Promise<void> {
  const before = messages.value.length
  try {
    await onLoadEarlier()
  } catch {
    // Nothing to report: loadEarlierPage has already toasted whatever it could
    // diagnose. This exists so the observer callback, which cannot await, does
    // not turn a rejection into an unhandled one (the F-9 shape).
    autoLoadExhausted = true
    return
  }
  if (messages.value.length === before) autoLoadExhausted = true
}

watch(
  loadEarlierRef,
  (el) => {
    disposeTopObserver?.()
    disposeTopObserver = null
    if (!el) return
    disposeTopObserver = observeTop(el, () => {
      // `loadingOlder` is the re-entrancy guard: reaching the threshold again
      // while a page is still in flight must not start a second one.
      if (autoLoadExhausted || loadingOlder.value || !hasOlderMessages.value) return
      void autoLoadEarlier()
    })
  },
  // `post`, so the feed element the observer roots on is committed before the
  // observer is built. A `pre` flush would root it on the viewport instead,
  // which scrolls independently of the feed.
  { immediate: true, flush: 'post' },
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
     script, which is where the rail's ceiling is derived from.

     The centre track is `minmax(0, 1fr)` for the reason spelled out on
     `.chatroom--mobile` below: a bare `1fr` is floored at `min-content` and so
     is sized by its widest descendant rather than by the space available. It
     never bit here only because a desktop window is wide enough to hide it. */
  grid-template-columns: 220px minmax(0, 1fr) 10px var(--chatroom-rail-w, 200px);
  grid-template-rows: 48px 1fr auto auto;
  height: 100%;
  overflow: hidden;
  /* Containing block for the compact band's overlay panels. Nothing else
     positions against it: the pill and the search panel both resolve against
     .chatroom__feed, which is relative in its own right. */
  position: relative;
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
  padding: var(--space-4);
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
  .msg-leave-active,
  .chatroom--compact .chatroom__agents,
  .chatroom--compact .chatroom__presence {
    transition: none;
  }
}

.chatroom__typing {
  grid-column: 2;
  grid-row: 3;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}

.chatroom__composer {
  grid-column: 2;
  grid-row: 4;
}

.chatroom__rail-handle {
  grid-column: 3;
  grid-row: 2 / -1;
}

/* Backdrop for whichever transient surface is active. A grid child rather than
   an inset overlay, so its extent is described by the same tracks the panels
   are: no second copy of the layout to keep in step.

   `--z-dropdown` minus one puts it under the panel it backs and over the feed.
   Neither .chatroom__feed nor .chatroom__presence creates a stacking context
   (both are `position: relative` at `z-index: auto`), so the search panel's own
   `--z-dropdown` still resolves against the same context and paints above. */
.chatroom__scrim {
  z-index: calc(var(--z-dropdown) - 1);
  /* 07-conversation.md:750 — the 0.2 dim, as one token rather than
     `--overlay-backdrop` under `opacity: 0.2`: those two multiply, and 0.45 at
     0.2 composites to 0.09, which does not read as a dim at all. Deliberately
     lighter than a modal backdrop even so, because the messages behind a search
     result have to stay legible; that is the point of a feed-scoped search. */
  background: var(--overlay-backdrop-inline);
  /* The token is a duration+easing shorthand already; appending an easing here
     produces two timing functions and the browser drops the declaration. */
  animation: chatroom-scrim-in var(--transition-normal);
}

/* Search is scoped to the feed it searches, so it dims the feed area only and
   leaves the composer live. */
.chatroom__scrim--search {
  grid-column: 2;
  grid-row: 2;
}

/* A compact rail overlay spans from below the header to the bottom of the
   window, so its scrim has to as well — otherwise the composer stays clickable
   under a panel that is meant to have taken over. */
.chatroom__scrim--rail {
  grid-column: 1 / -1;
  grid-row: 2 / -1;
}

.chatroom--mobile .chatroom__scrim--search,
.chatroom--compact .chatroom__scrim--search {
  grid-column: 1;
}

@keyframes chatroom-scrim-in {
  from {
    opacity: 0;
  }
}

@media (prefers-reduced-motion: reduce) {
  .chatroom__scrim {
    animation: none;
  }
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

/* Compact desktop (1024-1279), per 07-conversation.md:238 and the worked block
   at :241-252: both rails collapse to toggleable overlay panels so the feed
   gets the full width. At 1024 the fixed chrome was 220 + 10 + 200 = 430px,
   leaving the feed under 600px with no way to reclaim any of it.

   The handle track goes with them (there is nothing to size when the rail is an
   overlay), which is the one thing the spec block predates.

   This band is entirely inside `isDesktop`; `.chatroom--tablet` below is
   `< BP.lg`. The two never apply together, which is what keeps the deferred
   768-1023 rail change (FU-6) from being made here by accident. */
.chatroom--compact {
  grid-template-columns: 1fr;
}

.chatroom--compact .chatroom__feed,
.chatroom--compact .chatroom__typing,
.chatroom--compact .chatroom__composer {
  grid-column: 1;
}

.chatroom--compact .chatroom__agents,
.chatroom--compact .chatroom__presence {
  /* `auto` matters: an absolutely positioned grid child with a definite grid
     placement resolves against that grid area instead of the container. */
  grid-column: auto;
  grid-row: auto;
  position: absolute;
  top: 48px;
  bottom: 0;
  width: 280px;
  z-index: var(--z-dropdown);
  background: var(--color-bg);
  box-shadow: var(--shadow-lg);
  visibility: hidden;
  transition:
    transform var(--transition-normal),
    visibility var(--transition-normal);
}

.chatroom--compact .chatroom__agents {
  left: 0;
  border-right: 1px solid var(--color-border);
  transform: translateX(-100%);
}

.chatroom--compact .chatroom__presence {
  right: 0;
  border-left: 1px solid var(--color-border);
  transform: translateX(100%);
}

.chatroom--compact .chatroom__panel--open {
  transform: translateX(0);
  visibility: visible;
}

/* Tablet (768-1023): 2-column — agents rail + feed. Presence is a drawer. */
.chatroom--tablet {
  grid-template-columns: 200px 1fr;
}

/* Mobile: single column; side panels become drawers. The grid shrinks by the
   keyboard overlap so the composer stays visible above the virtual keyboard. */
/* `minmax(0, 1fr)`, never a bare `1fr`. A `1fr` track's automatic minimum is
   `min-content`, so the single mobile column was sized by the widest thing in
   it rather than by the viewport: at 375px the header's name, live pill and
   three icon buttons gave it a 498px min-content width, the track grew to
   match, and `.chatroom`'s `overflow: hidden` then clipped the pill and the
   buttons off the right edge. The empty state's `max-width: 400px` overflowed
   for the same reason - it was measuring against a 498px column, not a 375px
   screen. */
.chatroom--mobile {
  grid-template-columns: minmax(0, 1fr);
  grid-template-rows: 48px 1fr auto auto;
  height: calc(100% - var(--kb-inset, 0px));
}

.chatroom--mobile .chatroom__feed,
.chatroom--mobile .chatroom__typing,
.chatroom--mobile .chatroom__composer {
  grid-column: 1;
}
</style>
