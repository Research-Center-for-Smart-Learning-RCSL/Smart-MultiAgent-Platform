<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  SPageHeader, SCard, SButton, SInput, SBadge, SAlert,
  SLoadingSpinner, STooltip,
} from '@shared/ui'
import { useConfirmDialog, useInlineRename, useToast } from '@shared/composables'
import { isProblemWithType } from '@shared/transport'
import {
  PencilIcon, UserGroupIcon, ClipboardIcon,
  TrashIcon, ArrowPathIcon, UserIcon, BuildingOffice2Icon,
} from '@heroicons/vue/24/outline'
import { projectsApi, type Project } from '../api/projects'
import { tenancyKeys } from '../queries'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const toast = useToast()
const { confirm, prompt } = useConfirmDialog()
const qc = useQueryClient()

const projectId = computed(() => route.params.id as string)

const { data: project, isLoading, isError, refetch } = useQuery({
  queryKey: computed(() => tenancyKeys.project(projectId.value)),
  queryFn: () => projectsApi.get(projectId.value).then(r => r.data),
})

const isDeleted = computed(() => !!project.value?.deleted_at)

const rename = useInlineRename({
  current: () => project.value?.name ?? '',
  save: async (name) => {
    if (!project.value) return
    try {
      const { data } = await projectsApi.rename(project.value.id, name, project.value.version)
      qc.setQueryData(tenancyKeys.project(projectId.value), data)
    } catch (e: unknown) {
      if (isProblemWithType(e, '/tenancy/name-taken')) {
        toast.error(t('tenancy.project.nameTaken'))
      } else if (isProblemWithType(e, '/tenancy/version-mismatch')) {
        toast.warning(t('tenancy.common.versionConflict'))
        refetch()
      } else {
        toast.error(t('tenancy.project.loadError'))
      }
      throw e
    }
  },
})

async function deleteProject(): Promise<void> {
  if (!project.value) return
  const name = await prompt({
    title: t('tenancy.project.deleteTitle'),
    message: t('tenancy.project.deleteBody'),
    variant: 'error',
    confirmLabel: t('tenancy.project.deleteConfirm'),
    inputPattern: new RegExp(`^${project.value.name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`),
    inputErrorMessage: t('tenancy.project.deleteConfirm'),
  })
  if (name === null) return
  try {
    await projectsApi.remove(project.value.id)
    toast.success(t('tenancy.project.deleted'))
    router.push({ name: 'tenancy.projectList' })
  } catch {
    toast.error(t('tenancy.project.loadError'))
  }
}

async function restoreProject(): Promise<void> {
  if (!project.value) return
  const ok = await confirm({
    title: t('tenancy.project.restoreTitle'),
    message: t('tenancy.project.restoreBody'),
    variant: 'info',
    confirmLabel: t('tenancy.project.restoreConfirm'),
  })
  if (!ok) return
  try {
    await projectsApi.restore(project.value.id)
    qc.invalidateQueries({ queryKey: tenancyKeys.project(projectId.value) })
    toast.success(t('tenancy.project.restored'))
  } catch {
    toast.error(t('tenancy.project.loadError'))
  }
}

function copyId(): void {
  if (project.value) {
    navigator.clipboard.writeText(project.value.id)
  }
}

function formatDateTime(d: string | undefined): string {
  if (!d) return ''
  return d.replace('T', ' ').slice(0, 16)
}

function ownerDisplay(p: Project): string {
  if (p.owner_type === 'user') return t('tenancy.project.personal')
  return p.owner_name ?? p.owner_id?.slice(0, 8) ?? ''
}

const breadcrumbs = computed(() => [
  { label: t('tenancy.breadcrumb.home'), to: { name: 'tenancy.orgList' } },
  { label: t('tenancy.breadcrumb.projects'), to: { name: 'tenancy.projectList' } },
  { label: project.value?.name ?? '...' },
])
</script>

<template>
  <div>
    <SLoadingSpinner
      v-if="isLoading"
      :text="t('tenancy.common.loading')"
    />

    <SAlert
      v-else-if="isError"
      variant="danger"
    >
      {{ t('tenancy.project.loadError') }}
      <template #actions>
        <SButton
          variant="secondary"
          size="sm"
          @click="() => refetch()"
        >
          {{ t('app.confirm') }}
        </SButton>
      </template>
    </SAlert>

    <template v-else-if="project">
      <SPageHeader
        :title="project.name"
        :breadcrumbs="breadcrumbs"
      >
        <template #prepend>
          <SBadge
            v-if="isDeleted"
            variant="danger"
          >
            {{ t('tenancy.project.deleteConfirm') }}
          </SBadge>
        </template>

        <template #actions>
          <template v-if="!isDeleted">
            <SButton
              v-if="!rename.renaming.value"
              variant="ghost"
              size="sm"
              @click="rename.start"
            >
              <template #icon-left>
                <PencilIcon class="w-4 h-4" />
              </template>
              {{ t('app.save') }}
            </SButton>

            <SButton
              variant="secondary"
              as="router-link"
              :to="{ name: 'tenancy.projectMembers', params: { id: project.id } }"
            >
              <template #icon-left>
                <UserGroupIcon class="w-4 h-4" />
              </template>
              {{ t('tenancy.breadcrumb.members') }}
            </SButton>
          </template>

          <SButton
            v-if="isDeleted"
            variant="primary"
            @click="restoreProject"
          >
            <template #icon-left>
              <ArrowPathIcon class="w-4 h-4" />
            </template>
            {{ t('tenancy.project.restoreConfirm') }}
          </SButton>
        </template>
      </SPageHeader>

      <!-- Inline rename -->
      <div
        v-if="rename.renaming.value"
        class="rename-bar"
      >
        <form
          class="rename-form"
          @submit.prevent="rename.save"
        >
          <SInput
            v-model="rename.nameDraft.value"
            class="rename-input"
          />
          <SButton
            type="submit"
            variant="primary"
            size="sm"
          >
            {{ t('app.save') }}
          </SButton>
          <SButton
            variant="secondary"
            size="sm"
            @click="rename.cancel"
          >
            {{ t('app.cancel') }}
          </SButton>
        </form>
      </div>

      <!-- Soft-deleted alert -->
      <SAlert
        v-if="isDeleted"
        variant="warning"
        class="deleted-alert"
      >
        {{ t('tenancy.project.deletedBanner', {
          date: formatDateTime(project.deleted_at!),
          permanentDate: '—',
        }) }}
      </SAlert>

      <!-- Settings card -->
      <SCard
        variant="bordered"
        class="settings-card"
      >
        <h2 class="card-title">
          {{ t('tenancy.settings.title') }}
        </h2>
        <dl class="settings-list">
          <div class="settings-row">
            <dt>{{ t('tenancy.settings.projectId') }}</dt>
            <dd class="mono-value">
              {{ project.id.slice(0, 8) }}...
              <STooltip :content="project.id">
                <button
                  class="copy-btn"
                  @click="copyId"
                >
                  <ClipboardIcon class="w-4 h-4" />
                </button>
              </STooltip>
            </dd>
          </div>
          <div class="settings-row">
            <dt>{{ t('tenancy.settings.owner') }}</dt>
            <dd>
              <span class="owner-cell">
                <component
                  :is="project.owner_type === 'user' ? UserIcon : BuildingOffice2Icon"
                  class="w-4 h-4 owner-icon"
                />
                <router-link
                  v-if="project.owner_type === 'org'"
                  :to="{ name: 'tenancy.orgDetail', params: { id: project.owner_id } }"
                >
                  {{ ownerDisplay(project) }}
                </router-link>
                <span v-else>{{ ownerDisplay(project) }}</span>
              </span>
            </dd>
          </div>
          <div
            v-if="project.created_by_user_id"
            class="settings-row"
          >
            <dt>{{ t('tenancy.settings.createdBy') }}</dt>
            <dd>{{ project.created_by_user_id }}</dd>
          </div>
          <div class="settings-row">
            <dt>{{ t('tenancy.settings.created') }}</dt>
            <dd>{{ formatDateTime(project.created_at) }}</dd>
          </div>
          <div class="settings-row">
            <dt>{{ t('tenancy.settings.version') }}</dt>
            <dd>{{ project.version }}</dd>
          </div>
        </dl>
      </SCard>

      <!-- Danger zone -->
      <SCard
        v-if="!isDeleted"
        variant="bordered"
        class="danger-zone"
      >
        <h2 class="danger-title">
          {{ t('tenancy.dangerZone.title') }}
        </h2>
        <div class="danger-content">
          <div>
            <p class="danger-heading">
              {{ t('tenancy.project.deleteTitle') }}
            </p>
            <p class="danger-description">
              {{ t('tenancy.dangerZone.deleteProjectDescription') }}
            </p>
          </div>
          <SButton
            variant="danger"
            @click="deleteProject"
          >
            <template #icon-left>
              <TrashIcon class="w-4 h-4" />
            </template>
            {{ t('tenancy.project.deleteConfirm') }}
          </SButton>
        </div>
      </SCard>
    </template>
  </div>
</template>

<style scoped>
.rename-bar {
  margin-bottom: 24px;
}

.rename-form {
  display: flex;
  align-items: center;
  gap: 8px;
}

.rename-input {
  max-width: 360px;
}

.deleted-alert {
  margin-bottom: 24px;
}

.settings-card {
  margin-bottom: 24px;
}

.card-title {
  font-size: 1.125rem;
  font-weight: 600;
  margin-bottom: 16px;
}

.settings-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.settings-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.settings-row dt {
  font-size: 0.875rem;
  color: var(--color-muted);
}

.settings-row dd {
  font-size: 0.875rem;
  display: flex;
  align-items: center;
  gap: 4px;
}

.mono-value {
  font-family: "SF Mono", "Cascadia Code", "Fira Code", Consolas, monospace;
  font-size: 0.8125rem;
}

.copy-btn {
  display: inline-flex;
  align-items: center;
  padding: 2px;
  border: none;
  background: none;
  color: var(--color-muted);
  cursor: pointer;
  border-radius: 4px;
}

.copy-btn:hover {
  color: var(--color-accent);
  background: var(--color-surface);
}

.owner-cell {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.owner-icon {
  color: var(--color-muted);
}

.danger-zone {
  border-top: 2px solid var(--color-danger);
}

.danger-title {
  font-size: 1.125rem;
  font-weight: 600;
  color: var(--color-danger);
  margin-bottom: 16px;
}

.danger-content {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
}

.danger-heading {
  font-size: 0.875rem;
  font-weight: 600;
  margin-bottom: 4px;
}

.danger-description {
  font-size: 0.875rem;
  color: var(--color-muted);
}

@media (max-width: 480px) {
  .danger-content {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
