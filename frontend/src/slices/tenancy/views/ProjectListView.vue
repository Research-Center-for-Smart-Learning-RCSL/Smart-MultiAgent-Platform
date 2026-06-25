<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  SPageHeader, STabs, STable, SModal, SButton, SFormField,
  SInput, SSelect, SBadge, SEmptyState, SAlert,
} from '@shared/ui'
import { useToast } from '@shared/composables'
import { useSessionStore } from '@shared/stores/session'
import { isProblemWithType } from '@shared/transport'
import {
  FolderIcon, PlusIcon, ChevronRightIcon,
  UserIcon, BuildingOffice2Icon,
} from '@heroicons/vue/24/outline'
import { projectsApi, type Project, type ProjectOwnerType } from '../api/projects'
import { orgsApi } from '../api/orgs'
import { tenancyKeys } from '../queries'
import { formatDate } from '../utils/formatters'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const toast = useToast()
const session = useSessionStore()
const qc = useQueryClient()

const activeTab = ref<string>((route.query.scope as string) || 'all')

const { data: orgs } = useQuery({
  queryKey: tenancyKeys.orgs(),
  queryFn: () => orgsApi.list().then(r => r.data),
})

const tabs = computed(() => {
  const items = [
    { key: 'all', label: t('tenancy.project.tabAll') },
    { key: 'personal', label: t('tenancy.project.tabPersonal') },
  ]
  if (orgs.value) {
    for (const org of orgs.value) {
      items.push({ key: org.id, label: org.name })
    }
  }
  return items
})

const queryScope = computed(() => {
  if (activeTab.value === 'all') return { scope: undefined, id: undefined }
  if (activeTab.value === 'personal') return { scope: 'user' as const, id: session.me?.id }
  return { scope: 'org' as const, id: activeTab.value }
})

const { data: projects, isLoading, isError, refetch } = useQuery({
  queryKey: computed(() => tenancyKeys.projects(
    queryScope.value.scope ?? null,
    queryScope.value.id ?? null,
  )),
  queryFn: () => projectsApi.list(
    queryScope.value.scope,
    queryScope.value.id,
  ).then(r => r.data),
})

const showCreate = ref(false)
const createOwnerType = ref<ProjectOwnerType>('user')
const createOrgId = ref<string>('')
const createName = ref('')
const createError = ref<string | null>(null)
const creating = ref(false)

const ownerTypeOptions = [
  { value: 'user', label: t('tenancy.project.ownerPersonal') },
  { value: 'org', label: t('tenancy.project.ownerOrg') },
]

const orgSelectOptions = computed(() =>
  (orgs.value ?? []).map(o => ({ value: o.id, label: o.name })),
)

const columns = computed(() => [
  { key: 'name', label: t('tenancy.breadcrumb.projects'), sortable: true },
  { key: 'owner', label: t('tenancy.settings.owner'), sortable: true, width: '160px' },
  { key: 'role', label: t('tenancy.role.member'), sortable: true, width: '120px' },
  { key: 'created_at', label: t('tenancy.settings.created'), sortable: true, width: '120px' },
  { key: 'arrow', label: '', width: '40px' },
])

function ownerDisplay(project: Project): string {
  if (project.owner_type === 'user') return t('tenancy.project.personal')
  return project.owner_name ?? project.owner_id.slice(0, 8)
}

function emptyText(): string {
  if (activeTab.value === 'all') return t('tenancy.project.emptyAll')
  if (activeTab.value === 'personal') return t('tenancy.project.emptyPersonal')
  const org = orgs.value?.find(o => o.id === activeTab.value)
  return t('tenancy.project.emptyOrg', { org: org?.name ?? '' })
}

function openCreate(): void {
  createName.value = ''
  createOwnerType.value = 'user'
  createOrgId.value = orgSelectOptions.value[0]?.value ?? ''
  createError.value = null
  showCreate.value = true
}

async function submitCreate(): Promise<void> {
  const trimmed = createName.value.trim()
  if (!trimmed || creating.value) return

  const ownerId = createOwnerType.value === 'user'
    ? (session.me?.id ?? '')
    : createOrgId.value
  if (!ownerId) return

  creating.value = true
  createError.value = null
  try {
    const { data } = await projectsApi.create(createOwnerType.value, ownerId, trimmed)
    showCreate.value = false
    qc.invalidateQueries({ queryKey: ['tenancy', 'projects'] })
    toast.success(t('tenancy.project.created'))
    router.push({ name: 'tenancy.projectDetail', params: { id: data.id } })
  } catch (e: unknown) {
    if (isProblemWithType(e, '/tenancy/name-taken')) {
      createError.value = t('tenancy.project.nameTaken')
    } else {
      createError.value = t('tenancy.project.loadError')
    }
  } finally {
    creating.value = false
  }
}

function onRowClick(row: Project): void {
  router.push({ name: 'tenancy.projectDetail', params: { id: row.id } })
}

function onTabChange(key: string): void {
  activeTab.value = key
  router.replace({ query: { scope: key } })
}

const breadcrumbs = computed(() => [
  { label: t('tenancy.breadcrumb.home'), to: { name: 'tenancy.orgList' } },
])
</script>

<template>
  <div>
    <SPageHeader
      :title="t('tenancy.breadcrumb.projects')"
      :breadcrumbs="breadcrumbs"
    >
      <template #actions>
        <SButton
          variant="primary"
          @click="openCreate"
        >
          <template #icon-left>
            <PlusIcon class="w-4 h-4" />
          </template>
          {{ t('tenancy.project.createTitle') }}
        </SButton>
      </template>
    </SPageHeader>

    <STabs
      :model-value="activeTab"
      :tabs="tabs"
      class="project-tabs"
      @update:model-value="onTabChange"
    />

    <SAlert
      v-if="isError"
      variant="danger"
    >
      {{ t('tenancy.project.loadError') }}
      <template #actions>
        <SButton
          variant="secondary"
          size="sm"
          @click="() => refetch()"
        >
          {{ t('tenancy.common.retry') }}
        </SButton>
      </template>
    </SAlert>

    <STable
      v-else
      :columns="columns"
      :data="projects ?? []"
      :loading="isLoading"
      row-key="id"
      @row-click="onRowClick"
    >
      <template #cell-name="{ row }">
        <span class="project-name">{{ row.name }}</span>
      </template>

      <template #cell-owner="{ row }">
        <span class="owner-cell">
          <component
            :is="row.owner_type === 'user' ? UserIcon : BuildingOffice2Icon"
            class="w-4 h-4 owner-icon"
          />
          {{ ownerDisplay(row as Project) }}
        </span>
      </template>

      <template #cell-role>
        <SBadge variant="neutral">
          {{ t('tenancy.role.owner') }}
        </SBadge>
      </template>

      <template #cell-created_at="{ row }">
        {{ formatDate(row.created_at) }}
      </template>

      <template #cell-arrow>
        <ChevronRightIcon class="w-4 h-4 arrow-icon" />
      </template>

      <template #empty>
        <SEmptyState
          :icon="FolderIcon"
          :title="emptyText()"
        >
          <template #action>
            <SButton
              variant="primary"
              @click="openCreate"
            >
              {{ t('tenancy.project.createTitle') }}
            </SButton>
          </template>
        </SEmptyState>
      </template>
    </STable>

    <SModal
      :open="showCreate"
      :title="t('tenancy.project.createTitle')"
      size="sm"
      @close="showCreate = false"
    >
      <form @submit.prevent="submitCreate">
        <SFormField
          :label="t('tenancy.settings.owner')"
          name="ownerType"
          required
        >
          <SSelect
            v-model="createOwnerType"
            :options="ownerTypeOptions"
            :disabled="creating"
          />
        </SFormField>

        <SFormField
          v-if="createOwnerType === 'org'"
          :label="t('tenancy.breadcrumb.organizations')"
          name="orgId"
          required
        >
          <SSelect
            v-model="createOrgId"
            :options="orgSelectOptions"
            :disabled="creating"
          />
        </SFormField>

        <SFormField
          :label="t('tenancy.breadcrumb.projects')"
          name="projectName"
          :error="createError ?? undefined"
          :help="t('tenancy.project.nameHelp')"
          required
        >
          <SInput
            v-model="createName"
            :error="!!createError"
            :disabled="creating"
          />
        </SFormField>
      </form>

      <template #footer>
        <SButton
          variant="secondary"
          :disabled="creating"
          @click="showCreate = false"
        >
          {{ t('app.cancel') }}
        </SButton>
        <SButton
          variant="primary"
          :loading="creating"
          :disabled="creating || !createName.trim()"
          @click="submitCreate"
        >
          {{ t('tenancy.project.createTitle') }}
        </SButton>
      </template>
    </SModal>
  </div>
</template>

<style scoped>
.project-tabs {
  margin-bottom: 16px;
}

.project-name {
  font-weight: 500;
}

.owner-cell {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.owner-icon {
  color: var(--color-muted);
}

.arrow-icon {
  color: var(--color-muted);
}
</style>
