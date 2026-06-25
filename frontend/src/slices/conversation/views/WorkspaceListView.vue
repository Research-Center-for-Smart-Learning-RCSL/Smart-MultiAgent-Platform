<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  PlusIcon,
  Square3Stack3DIcon,
  TrashIcon,
  EllipsisVerticalIcon,
} from '@heroicons/vue/24/outline'
import {
  SPageHeader,
  SSearchInput,
  SCard,
  SButton,
  SDropdown,
  SModal,
  SFormField,
  SInput,
  SEmptyState,
  SAlert,
  SSkeleton,
} from '@shared/ui'
import { useConfirmDialog, useToast } from '@shared/composables'
import {
  createWorkspace,
  deleteWorkspace,
  listWorkspaces,
} from '../api'
import { convKeys } from '../queries'
import { workspaceCreateSchema } from '../types/schemas'
import { formatDate } from '../utils/format'
import type { Workspace } from '../types'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const qc = useQueryClient()
const toast = useToast()
const { confirm } = useConfirmDialog()
const projectId = route.params.projectId as string

const search = ref('')

const query = useQuery({
  queryKey: convKeys.workspaces(projectId),
  queryFn: () => listWorkspaces(projectId),
})

const workspaces = computed<Workspace[]>(() => query.data.value ?? [])
const loading = computed(() => query.isLoading.value)

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return workspaces.value
  return workspaces.value.filter((w) => w.name.toLowerCase().includes(q))
})

// ---- create ---------------------------------------------------------------

const showCreate = ref(false)
const createName = ref('')
const createError = ref<string | null>(null)

function openCreate(): void {
  createName.value = ''
  createError.value = null
  showCreate.value = true
}

const createMutation = useMutation({
  mutationFn: (name: string) => createWorkspace(projectId, { name }),
  onSuccess: (ws) => {
    showCreate.value = false
    qc.invalidateQueries({ queryKey: convKeys.workspaces(projectId) })
    toast.success(t('conversation.workspaces.created'))
    router.push({ name: 'conversation.chatrooms', params: { workspaceId: ws.id } })
  },
  onError: () => toast.error(t('conversation.workspaces.createFailed')),
})

function submitCreate(): void {
  const parsed = workspaceCreateSchema.safeParse({ name: createName.value })
  if (!parsed.success) {
    createError.value = t('conversation.workspaces.nameInvalid')
    return
  }
  createError.value = null
  createMutation.mutate(parsed.data.name)
}

// ---- delete ---------------------------------------------------------------

const deleteMutation = useMutation({
  mutationFn: (id: string) => deleteWorkspace(id),
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: convKeys.workspaces(projectId) })
    toast.success(t('conversation.workspaces.deleted'))
  },
  onError: () => toast.error(t('conversation.workspaces.deleteFailed')),
})

const actionItems = computed(() => [
  { key: 'delete', label: t('conversation.workspaces.delete'), icon: TrashIcon, danger: true },
])

async function onAction(key: string, ws: Workspace): Promise<void> {
  if (key !== 'delete') return
  const ok = await confirm({
    title: t('conversation.workspaces.deleteTitle'),
    message: t('conversation.workspaces.deleteConfirm'),
    variant: 'error',
    confirmLabel: t('conversation.workspaces.delete'),
  })
  if (!ok) return
  deleteMutation.mutate(ws.id)
}

function openWorkspace(ws: Workspace): void {
  router.push({ name: 'conversation.chatrooms', params: { workspaceId: ws.id } })
}
</script>

<template>
  <main class="p-6">
    <SPageHeader :title="t('conversation.workspaces.title')">
      <template #actions>
        <SButton
          variant="primary"
          data-testid="create-workspace"
          @click="openCreate"
        >
          <template #icon-left>
            <PlusIcon class="w-4 h-4" />
          </template>
          {{ t('conversation.workspaces.create') }}
        </SButton>
      </template>
    </SPageHeader>

    <div class="mt-6">
      <SSearchInput
        v-model="search"
        :placeholder="t('conversation.workspaces.searchPlaceholder')"
        class="w-64"
      />
    </div>

    <SAlert
      v-if="query.error.value"
      variant="danger"
      class="mt-4"
    >
      {{ t('conversation.workspaces.loadFailed') }}
      <template #actions>
        <SButton
          variant="ghost"
          size="sm"
          @click="query.refetch()"
        >
          {{ t('conversation.workspaces.retry') }}
        </SButton>
      </template>
    </SAlert>

    <!-- Loading skeletons -->
    <div
      v-if="loading"
      class="ws-grid mt-6"
    >
      <SSkeleton
        v-for="n in 4"
        :key="n"
        variant="rect"
        height="120px"
      />
    </div>

    <!-- Empty -->
    <SEmptyState
      v-else-if="filtered.length === 0"
      :icon="Square3Stack3DIcon"
      :title="t('conversation.workspaces.emptyTitle')"
      :text="t('conversation.workspaces.emptyDescription')"
      class="mt-10"
    >
      <template #action>
        <SButton
          variant="primary"
          @click="openCreate"
        >
          {{ t('conversation.workspaces.create') }}
        </SButton>
      </template>
    </SEmptyState>

    <!-- Card grid -->
    <div
      v-else
      class="ws-grid mt-6"
    >
      <SCard
        v-for="ws in filtered"
        :key="ws.id"
        class="ws-card"
        @click="openWorkspace(ws)"
      >
        <div class="ws-card__top">
          <Square3Stack3DIcon class="ws-card__icon" />
          <SDropdown
            :items="actionItems"
            placement="bottom-end"
            @select="onAction($event, ws)"
          >
            <template #trigger>
              <SButton
                variant="ghost"
                icon-only
                size="sm"
                :aria-label="t('conversation.workspaces.actions')"
                @click.stop
              >
                <EllipsisVerticalIcon class="w-4 h-4" />
              </SButton>
            </template>
          </SDropdown>
        </div>
        <p class="ws-card__name">
          {{ ws.name }}
        </p>
        <p class="ws-card__meta">
          {{ t('conversation.workspaces.createdOn', { date: formatDate(ws.created_at) }) }}
        </p>
      </SCard>
    </div>

    <SModal
      :open="showCreate"
      :title="t('conversation.workspaces.createTitle')"
      size="sm"
      @close="showCreate = false"
    >
      <form @submit.prevent="submitCreate">
        <SFormField
          :label="t('conversation.workspaces.name')"
          name="workspaceName"
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
      </form>
      <template #footer>
        <SButton
          variant="secondary"
          :disabled="createMutation.isPending.value"
          @click="showCreate = false"
        >
          {{ t('conversation.workspaces.cancel') }}
        </SButton>
        <SButton
          variant="primary"
          :loading="createMutation.isPending.value"
          :disabled="createMutation.isPending.value || !createName.trim()"
          @click="submitCreate"
        >
          {{ t('conversation.workspaces.create') }}
        </SButton>
      </template>
    </SModal>
  </main>
</template>

<style scoped>
.ws-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}

.ws-card {
  cursor: pointer;
  transition: box-shadow var(--transition-fast);
}

.ws-card:hover {
  box-shadow: var(--shadow-md);
}

.ws-card__top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
}

.ws-card__icon {
  width: 24px;
  height: 24px;
  color: var(--color-muted);
}

.ws-card__name {
  margin-top: 12px;
  font-size: 16px;
  font-weight: 600;
  color: var(--color-fg);
}

.ws-card__meta {
  margin-top: 4px;
  font-size: 12px;
  color: var(--color-muted);
}
</style>
