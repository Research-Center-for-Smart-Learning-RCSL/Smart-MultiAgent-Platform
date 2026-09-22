<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  PlusIcon,
  ChatBubbleLeftRightIcon,
  Cog6ToothIcon,
  ShareIcon,
  TrashIcon,
  EllipsisVerticalIcon,
} from '@heroicons/vue/24/outline'
import {
  SPageHeader,
  SSearchInput,
  STable,
  SBadge,
  SButton,
  SDropdown,
  SEmptyState,
  SAlert,
} from '@shared/ui'
import { useConfirmDialog, useToast, useListStagger } from '@shared/composables'
import { useProjectRole } from '@slices/tenancy'
import {
  deleteChatroom,
  getWorkspace,
  listChatrooms,
} from '../api'
import { convKeys } from '../queries'
import { useChatroomCreate } from '../composables/useChatroomCreate'
import ChatroomCreateModal from '../components/ChatroomCreateModal.vue'
import { formatDate } from '../utils/format'
import type { Chatroom } from '../types'
import type { Column } from '@shared/ui/STable.vue'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const qc = useQueryClient()
const toast = useToast()
const { confirm } = useConfirmDialog()
const workspaceId = route.params.workspaceId as string

const search = ref('')

const workspaceQuery = useQuery({
  queryKey: convKeys.workspace(workspaceId),
  queryFn: () => getWorkspace(workspaceId),
})

const query = useQuery({
  queryKey: convKeys.chatrooms(workspaceId),
  queryFn: () => listChatrooms(workspaceId),
})

// Every workflow read is Admin-or-project-owner ([R14.10], dossier
// 2026-08-20-orchestration-room-scoped-reads), so this entry point would land a
// plain member on a page that redirects them straight back. Hide it instead.
const { isAuthorized: canOpenWorkflows } = useProjectRole(
  computed(() => workspaceQuery.data.value?.project_id),
)

const rooms = computed<Chatroom[]>(() => query.data.value ?? [])
const loading = computed(() => query.isLoading.value)
const staggerClass = useListStagger(loading)

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return rooms.value
  return rooms.value.filter((r) => r.name.toLowerCase().includes(q))
})

// STable's generic constrains T to Record<string, unknown>; Chatroom has no
// index signature by design, so intersect it in only for this cast (the
// runtime shape is unchanged — plain chatroom objects from the API).
type ChatroomRow = Chatroom & Record<string, unknown>

const tableRows = computed<ChatroomRow[]>(() => filtered.value as unknown as ChatroomRow[])

const breadcrumbs = computed(() => {
  const ws = workspaceQuery.data.value
  return [
    {
      label: t('conversation.workspaces.title'),
      ...(ws && {
        to: { name: 'conversation.workspaces', params: { projectId: ws.project_id } },
      }),
    },
  ]
})

const pageTitle = computed(
  () => workspaceQuery.data.value?.name ?? t('conversation.chatrooms.title'),
)

interface AccessBadge {
  label: string
  variant: 'info' | 'warning' | 'neutral'
}

function accessBadges(room: Chatroom): AccessBadge[] {
  const badges: AccessBadge[] = []
  if (room.allow_project_owners_only) {
    badges.push({ label: t('conversation.chatrooms.access.ownersOnly'), variant: 'warning' })
  } else {
    if (room.allow_org_members) {
      badges.push({ label: t('conversation.chatrooms.access.orgMembers'), variant: 'info' })
    }
    if (room.allow_project_members) {
      badges.push({ label: t('conversation.chatrooms.access.members'), variant: 'info' })
    }
  }
  if (room.allow_guest_links) {
    badges.push({ label: t('conversation.chatrooms.access.guestLink'), variant: 'neutral' })
  }
  return badges
}

const columns = computed<Column[]>(() => [
  { key: 'name', label: t('conversation.chatrooms.colName'), sortable: true },
  { key: 'access', label: t('conversation.chatrooms.colAccess') },
  { key: 'created_at', label: t('conversation.chatrooms.colCreated'), width: '140px' },
  { key: 'actions', label: '', width: '48px', align: 'right' },
])

const actionItems = computed(() => [
  { key: 'settings', label: t('conversation.chatrooms.settings'), icon: Cog6ToothIcon },
  { key: 'divider', label: '', divider: true },
  { key: 'delete', label: t('conversation.chatrooms.delete'), icon: TrashIcon, danger: true },
])

function openRoom(room: Chatroom): void {
  router.push({ name: 'conversation.chatroom', params: { chatroomId: room.id } })
}

function openSettings(room: Chatroom): void {
  router.push({ name: 'conversation.chatroom.settings', params: { chatroomId: room.id } })
}

function onRowClick(row: Chatroom): void {
  openRoom(row)
}

// ---- delete ---------------------------------------------------------------

const deleteMutation = useMutation({
  mutationFn: (id: string) => deleteChatroom(id),
  onSuccess: () => {
    // F-4: the prefix, not this workspace's list alone. `recentChatrooms` nests
    // under it and is the sidebar rail, which has a 60s staleTime in a component
    // that never unmounts — so a room deleted here stayed clickable there and
    // routed the user into a room the server no longer serves. The prefix still
    // covers this view's own list, which is a strict extension of it.
    qc.invalidateQueries({ queryKey: convKeys.chatroomsAll() })
    toast.success(t('conversation.chatrooms.deleted'))
  },
  onError: () => toast.error(t('conversation.chatrooms.deleteFailed')),
})

async function onAction(key: string, room: Chatroom): Promise<void> {
  if (key === 'settings') {
    openSettings(room)
  } else if (key === 'delete') {
    const ok = await confirm({
      title: t('conversation.chatrooms.deleteTitle'),
      message: t('conversation.chatrooms.deleteConfirm'),
      variant: 'error',
      confirmLabel: t('conversation.chatrooms.delete'),
    })
    if (ok) deleteMutation.mutate(room.id)
  }
}

// ---- create ---------------------------------------------------------------

const {
  showCreate,
  createName,
  createError,
  createFlags,
  openCreate,
  submitCreate,
  isCreating,
} = useChatroomCreate(() => workspaceId)
</script>

<template>
  <div>
    <SPageHeader
      :title="pageTitle"
      :breadcrumbs="breadcrumbs"
    >
      <template #actions>
        <SButton
          v-if="canOpenWorkflows"
          variant="secondary"
          :to="{ name: 'workflow.list', params: { workspaceId } }"
          as="router-link"
        >
          <template #icon-left>
            <ShareIcon class="w-4 h-4" />
          </template>
          {{ t('conversation.chatrooms.workflows') }}
        </SButton>
        <SButton
          variant="primary"
          data-testid="create-chatroom"
          @click="openCreate"
        >
          <template #icon-left>
            <PlusIcon class="w-4 h-4" />
          </template>
          {{ t('conversation.chatrooms.create') }}
        </SButton>
      </template>
    </SPageHeader>

    <div class="mt-6">
      <SSearchInput
        v-model="search"
        :placeholder="t('conversation.chatrooms.searchPlaceholder')"
        class="w-64"
      />
    </div>

    <SAlert
      v-if="query.error.value"
      variant="danger"
      class="mt-4"
    >
      {{ t('conversation.chatrooms.loadFailed') }}
      <template #actions>
        <SButton
          variant="ghost"
          size="sm"
          @click="query.refetch()"
        >
          {{ t('conversation.chatrooms.retry') }}
        </SButton>
      </template>
    </SAlert>

    <!-- One table for all viewports: STable's card-list mode replaces the
         old hand-rolled mobile card branch (which also skipped empty states). -->
    <STable
      :columns="columns"
      :data="tableRows"
      :loading="loading"
      row-key="id"
      sticky-header
      responsive-mode="card-list"
      class="mt-6"
      :class="staggerClass"
      @row-click="onRowClick"
    >
      <template #cell-name="{ row }">
        <span class="room-name">
          <ChatBubbleLeftRightIcon class="w-4 h-4 room-name__icon" />
          <span class="font-medium cursor-pointer text-[var(--color-accent)]">
            {{ row.name }}
          </span>
        </span>
      </template>

      <template #cell-access="{ row }">
        <span class="flex flex-wrap gap-1">
          <SBadge
            v-for="b in accessBadges(row as Chatroom)"
            :key="b.label"
            :variant="b.variant"
            size="sm"
          >
            {{ b.label }}
          </SBadge>
        </span>
      </template>

      <template #cell-created_at="{ row }">
        {{ formatDate(row.created_at) }}
      </template>

      <template #actions="{ row }">
        <SDropdown
          :items="actionItems"
          placement="bottom-end"
          @select="onAction($event, row)"
        >
          <template #trigger>
            <SButton
              variant="ghost"
              icon-only
              size="sm"
              :aria-label="t('conversation.chatrooms.actions')"
            >
              <EllipsisVerticalIcon class="w-4 h-4" />
            </SButton>
          </template>
        </SDropdown>
      </template>

      <template #empty>
        <SEmptyState
          :icon="ChatBubbleLeftRightIcon"
          :title="t('conversation.chatrooms.emptyTitle')"
          :text="t('conversation.chatrooms.emptyDescription')"
        >
          <template #action>
            <SButton
              variant="primary"
              @click="openCreate"
            >
              {{ t('conversation.chatrooms.create') }}
            </SButton>
          </template>
        </SEmptyState>
      </template>
    </STable>

    <ChatroomCreateModal
      :open="showCreate"
      :name="createName"
      :error="createError"
      :flags="createFlags"
      :is-pending="isCreating.value"
      @close="showCreate = false"
      @submit="submitCreate"
      @update:name="createName = $event"
      @update:flags="(key: string, val: boolean) => (createFlags as Record<string, boolean>)[key] = val"
    />
  </div>
</template>

<style scoped>
.room-name {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1-5);
}

.room-name__icon {
  color: var(--color-accent);
  flex-shrink: 0;
}

</style>
