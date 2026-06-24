<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  PlusIcon,
  EyeIcon,
  TrashIcon,
  RectangleGroupIcon,
  EllipsisVerticalIcon,
} from '@heroicons/vue/24/outline'
import {
  SPageHeader,
  STable,
  SButton,
  SDropdown,
  SModal,
  SFormField,
  SInput,
  SEmptyState,
  SAlert,
} from '@shared/ui'
import { useConfirmDialog, useToast } from '@shared/composables'
import { useKeyGroups } from '../composables/useKeyGroups'
import type { Column } from '@shared/ui/STable.vue'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const toast = useToast()
const { confirm } = useConfirmDialog()
const projectId = computed(() => route.params.projectId as string)
const { groups, error, reload, create, remove } = useKeyGroups(() => projectId.value)

const showCreate = ref(false)
const newName = ref('')
const createError = ref('')
const creating = ref(false)

const columns = computed<Column[]>(() => [
  { key: 'name', label: t('keys.groups.name') },
  { key: 'members', label: t('keys.groups.members'), width: '120px' },
  { key: 'created_at', label: t('keys.groups.created'), width: '160px' },
  { key: 'actions', label: '', width: '80px', align: 'right' },
])

const actionItems = computed(() => [
  { key: 'view', label: t('keys.groups.viewGroup'), icon: EyeIcon },
  { key: 'delete', label: t('keys.groups.deleteGroup'), icon: TrashIcon, danger: true },
])

async function onCreate() {
  const n = newName.value.trim()
  if (!n) {
    createError.value = t('keys.groups.nameRequired')
    return
  }
  if (n.length > 200) {
    createError.value = t('keys.groups.nameTooLong')
    return
  }
  creating.value = true
  createError.value = ''
  try {
    await create(n)
    showCreate.value = false
    newName.value = ''
  } catch {
    createError.value = t('keys.groups.createFailed')
  } finally {
    creating.value = false
  }
}

async function onDelete(id: string) {
  const group = groups.value.find((g) => g.id === id)
  const ok = await confirm({
    title: t('keys.groups.deleteTitle'),
    message: t('keys.groups.deleteBody', { name: group?.name ?? '' }),
    confirmLabel: t('keys.groups.delete'),
    variant: 'error',
  })
  if (!ok) return
  try {
    await remove(id)
  } catch {
    toast.error(t('keys.groups.deleteFailed'))
  }
}

function onAction(key: string, row: { id: string }) {
  if (key === 'view') {
    router.push({ name: 'keys.groupDetail', params: { projectId: projectId.value, id: row.id } })
  } else if (key === 'delete') {
    void onDelete(row.id)
  }
}

function onRowClick(row: { id: string }) {
  router.push({ name: 'keys.groupDetail', params: { projectId: projectId.value, id: row.id } })
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

onMounted(reload)
watch(projectId, reload)
</script>

<template>
  <main class="p-6">
    <SPageHeader :title="$t('keys.groups.listTitle')">
      <template #description>
        {{ $t('keys.groups.listDescription') }}
      </template>
      <template #actions>
        <SButton
          variant="primary"
          @click="showCreate = true"
        >
          <template #icon-left>
            <PlusIcon class="w-4 h-4" />
          </template>
          {{ $t('keys.groups.create') }}
        </SButton>
      </template>
    </SPageHeader>

    <SAlert
      v-if="error"
      variant="danger"
      class="mt-4"
    >
      {{ error }}
    </SAlert>

    <STable
      :columns="columns"
      :data="groups"
      row-key="id"
      class="mt-6"
      @row-click="onRowClick"
    >
      <template #cell-name="{ row }">
        <router-link
          :to="{ name: 'keys.groupDetail', params: { projectId, id: row.id } }"
          class="text-[var(--color-accent)] hover:underline"
        >
          {{ row.name }}
        </router-link>
      </template>

      <template #cell-members="{ row }">
        {{ $t('keys.groups.memberCount', { n: row.member_count ?? 0 }) }}
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
              :aria-label="$t('keys.list.actions')"
            >
              <EllipsisVerticalIcon class="w-4 h-4" />
            </SButton>
          </template>
        </SDropdown>
      </template>

      <template #empty>
        <SEmptyState
          :icon="RectangleGroupIcon"
          :title="$t('keys.groups.emptyTitle')"
          :text="$t('keys.groups.emptyDescription')"
        >
          <template #action>
            <SButton
              variant="primary"
              @click="showCreate = true"
            >
              {{ $t('keys.groups.create') }}
            </SButton>
          </template>
        </SEmptyState>
      </template>
    </STable>

    <!-- Create Group Modal -->
    <SModal
      :open="showCreate"
      :title="$t('keys.groups.createTitle')"
      size="sm"
      @close="showCreate = false; newName = ''; createError = ''"
    >
      <form
        id="create-group-form"
        @submit.prevent="onCreate"
      >
        <SFormField
          :label="$t('keys.groups.nameLabel')"
          name="group-name"
          :error="createError"
          required
        >
          <SInput
            v-model="newName"
            :placeholder="$t('keys.groups.namePlaceholder')"
            :error="!!createError"
            data-testid="group-name"
          />
        </SFormField>
      </form>

      <template #footer>
        <div class="flex justify-end gap-3">
          <SButton
            variant="secondary"
            @click="showCreate = false; newName = ''; createError = ''"
          >
            {{ $t('app.cancel') }}
          </SButton>
          <SButton
            variant="primary"
            type="submit"
            form="create-group-form"
            :loading="creating"
            data-testid="group-create"
          >
            {{ $t('keys.groups.create') }}
          </SButton>
        </div>
      </template>
    </SModal>
  </main>
</template>
