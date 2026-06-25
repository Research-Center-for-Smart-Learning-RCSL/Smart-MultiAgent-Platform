<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  PlusIcon,
  ChatBubbleLeftRightIcon,
  Cog6ToothIcon,
  TrashIcon,
  EllipsisVerticalIcon,
} from '@heroicons/vue/24/outline'
import {
  SPageHeader,
  SSearchInput,
  STable,
  SCard,
  SBadge,
  SButton,
  SDropdown,
  SModal,
  SFormField,
  SInput,
  SToggle,
  SEmptyState,
  SAlert,
} from '@shared/ui'
import { useConfirmDialog, useToast, useBreakpoint } from '@shared/composables'
import {
  createChatroom,
  deleteChatroom,
  getWorkspace,
  listChatrooms,
} from '../api'
import { convKeys } from '../queries'
import { chatroomCreateSchema, type ChatroomCreateInput } from '../types/schemas'
import { formatDate } from '../utils/format'
import type { Chatroom } from '../types'
import type { Column } from '@shared/ui/STable.vue'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const qc = useQueryClient()
const toast = useToast()
const { confirm } = useConfirmDialog()
const { isMobile } = useBreakpoint()
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

const rooms = computed<Chatroom[]>(() => query.data.value ?? [])
const loading = computed(() => query.isLoading.value)

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return rooms.value
  return rooms.value.filter((r) => r.name.toLowerCase().includes(q))
})

const breadcrumbs = computed(() => {
  const ws = workspaceQuery.data.value
  return [
    {
      label: t('conversation.workspaces.title'),
      to: ws
        ? { name: 'conversation.workspaces', params: { projectId: ws.project_id } }
        : undefined,
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
    qc.invalidateQueries({ queryKey: convKeys.chatrooms(workspaceId) })
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

const showCreate = ref(false)
const createName = ref('')
const createError = ref<string | null>(null)
const createFlags = reactive({
  allow_org_members: false,
  allow_project_members: true,
  allow_project_owners_only: false,
  allow_guest_links: false,
})

function openCreate(): void {
  createName.value = ''
  createError.value = null
  createFlags.allow_org_members = false
  createFlags.allow_project_members = true
  createFlags.allow_project_owners_only = false
  createFlags.allow_guest_links = false
  showCreate.value = true
}

const createMutation = useMutation({
  mutationFn: (payload: ChatroomCreateInput) => createChatroom(workspaceId, payload),
  onSuccess: (room) => {
    showCreate.value = false
    qc.invalidateQueries({ queryKey: convKeys.chatrooms(workspaceId) })
    toast.success(t('conversation.chatrooms.created'))
    openRoom(room)
  },
  onError: () => toast.error(t('conversation.chatrooms.createFailed')),
})

function submitCreate(): void {
  const parsed = chatroomCreateSchema.safeParse({ name: createName.value, ...createFlags })
  if (!parsed.success) {
    createError.value = t('conversation.chatrooms.nameInvalid')
    return
  }
  createError.value = null
  createMutation.mutate(parsed.data)
}
</script>

<template>
  <main class="p-6">
    <SPageHeader
      :title="pageTitle"
      :breadcrumbs="breadcrumbs"
    >
      <template #actions>
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

    <!-- Mobile: card layout -->
    <div
      v-if="isMobile && !loading && filtered.length > 0"
      class="mt-6 space-y-3"
    >
      <SCard
        v-for="room in filtered"
        :key="room.id"
        class="cursor-pointer"
        @click="openRoom(room)"
      >
        <div class="flex items-center justify-between">
          <div class="room-name">
            <ChatBubbleLeftRightIcon class="w-4 h-4 room-name__icon" />
            <span class="font-medium">{{ room.name }}</span>
          </div>
          <SDropdown
            :items="actionItems"
            placement="bottom-end"
            @select="onAction($event, room)"
          >
            <template #trigger>
              <SButton
                variant="ghost"
                icon-only
                size="sm"
                :aria-label="t('conversation.chatrooms.actions')"
                @click.stop
              >
                <EllipsisVerticalIcon class="w-4 h-4" />
              </SButton>
            </template>
          </SDropdown>
        </div>
        <div class="mt-2 flex flex-wrap gap-1">
          <SBadge
            v-for="b in accessBadges(room)"
            :key="b.label"
            :variant="b.variant"
            size="sm"
          >
            {{ b.label }}
          </SBadge>
        </div>
      </SCard>
    </div>

    <!-- Desktop: table layout -->
    <STable
      v-if="!isMobile"
      :columns="columns"
      :data="filtered"
      :loading="loading"
      row-key="id"
      sticky-header
      class="mt-6"
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

    <SModal
      :open="showCreate"
      :title="t('conversation.chatrooms.createTitle')"
      size="md"
      @close="showCreate = false"
    >
      <form @submit.prevent="submitCreate">
        <SFormField
          :label="t('conversation.chatrooms.colName')"
          name="chatroomName"
          :error="createError ?? undefined"
          required
        >
          <SInput
            v-model="createName"
            :error="!!createError"
            :disabled="createMutation.isPending.value"
            maxlength="200"
          />
        </SFormField>

        <fieldset class="access-fieldset">
          <legend class="access-fieldset__legend">
            {{ t('conversation.chatrooms.colAccess') }}
          </legend>

          <div
            class="access-row"
            :class="{ 'access-row--dimmed': createFlags.allow_project_owners_only }"
          >
            <span>{{ t('conversation.settings.allowOrgMembers') }}</span>
            <SToggle
              v-model="createFlags.allow_org_members"
              :disabled="createFlags.allow_project_owners_only"
            />
          </div>
          <div
            class="access-row"
            :class="{ 'access-row--dimmed': createFlags.allow_project_owners_only }"
          >
            <span>{{ t('conversation.settings.allowProjectMembers') }}</span>
            <SToggle
              v-model="createFlags.allow_project_members"
              :disabled="createFlags.allow_project_owners_only"
            />
          </div>
          <div class="access-row">
            <span>{{ t('conversation.settings.allowProjectOwnersOnly') }}</span>
            <SToggle v-model="createFlags.allow_project_owners_only" />
          </div>
          <div class="access-row">
            <span>{{ t('conversation.settings.allowGuestLinks') }}</span>
            <SToggle v-model="createFlags.allow_guest_links" />
          </div>
        </fieldset>
      </form>

      <template #footer>
        <SButton
          variant="secondary"
          :disabled="createMutation.isPending.value"
          @click="showCreate = false"
        >
          {{ t('conversation.chatrooms.cancel') }}
        </SButton>
        <SButton
          variant="primary"
          :loading="createMutation.isPending.value"
          :disabled="createMutation.isPending.value || !createName.trim()"
          @click="submitCreate"
        >
          {{ t('conversation.chatrooms.create') }}
        </SButton>
      </template>
    </SModal>
  </main>
</template>

<style scoped>
.room-name {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.room-name__icon {
  color: var(--color-accent);
  flex-shrink: 0;
}

.access-fieldset {
  border: none;
  margin: 8px 0 0;
  padding: 0;
}

.access-fieldset__legend {
  font-size: 0.875rem;
  font-weight: 500;
  color: var(--color-fg);
  margin-bottom: 8px;
}

.access-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 0;
  font-size: 0.875rem;
  color: var(--color-fg);
}

.access-row--dimmed {
  opacity: 0.5;
}
</style>
