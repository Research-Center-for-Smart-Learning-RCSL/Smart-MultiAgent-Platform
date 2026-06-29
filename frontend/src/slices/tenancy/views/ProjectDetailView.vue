<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  SPageHeader, SCard, SButton, SInput, SBadge, SAlert,
  SLoadingSpinner, STooltip,
} from '@shared/ui'
import { useInlineRename, useToast } from '@shared/composables'
import { useSessionStore } from '@shared/stores/session'
import { isProblemWithType } from '@shared/transport'
import { INPUT_LIMITS } from '@shared/constants/inputLimits'
import {
  PencilIcon, UserGroupIcon, ClipboardIcon,
  TrashIcon, ArrowPathIcon, UserIcon, BuildingOffice2Icon,
} from '@heroicons/vue/24/outline'
import { projectsApi, type Project, type ProjectMember } from '../api/projects'
import { tenancyKeys } from '../queries'
import { formatDateTime } from '../utils/formatters'
import { useEntityLifecycle } from '../composables/useEntityLifecycle'

const { t } = useI18n()
const route = useRoute()
const toast = useToast()
const session = useSessionStore()
const qc = useQueryClient()

const projectId = computed(() => route.params.id as string)

const { data: project, isLoading, isError, refetch } = useQuery({
  queryKey: computed(() => tenancyKeys.project(projectId.value)),
  queryFn: () => projectsApi.get(projectId.value).then(r => r.data),
})

const { data: members } = useQuery({
  queryKey: computed(() => tenancyKeys.projectMembers(projectId.value)),
  queryFn: () => projectsApi.listMembers(projectId.value).then(r => r.data),
})

const myMembership = computed<ProjectMember | null>(() => {
  if (!members.value || !session.me) return null
  return members.value.find(m => m.user_id === session.me!.id) ?? null
})

const isOwner = computed(() => myMembership.value?.role === 'owner')
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

const lifecycle = useEntityLifecycle({
  entityName: () => project.value?.name ?? '',
  deleteTitle: () => t('tenancy.project.deleteTitle'),
  deleteBody: () => t('tenancy.project.deleteBody'),
  deleteConfirmLabel: () => t('tenancy.project.deleteConfirm'),
  deletedToast: () => t('tenancy.project.deleted'),
  restoreTitle: () => t('tenancy.project.restoreTitle'),
  restoreBody: () => t('tenancy.project.restoreBody'),
  restoreConfirmLabel: () => t('tenancy.project.restoreConfirm'),
  restoredToast: () => t('tenancy.project.restored'),
  errorToast: () => t('tenancy.project.loadError'),
  removeApi: (id) => projectsApi.remove(id),
  restoreApi: (id) => projectsApi.restore(id),
  queryKey: () => tenancyKeys.project(projectId.value),
  qc,
  listRoute: 'tenancy.projectList',
})

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
          {{ t('tenancy.common.retry') }}
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
              v-if="isOwner && !rename.renaming.value"
              variant="ghost"
              size="sm"
              @click="rename.start"
            >
              <template #icon-left>
                <PencilIcon class="w-4 h-4" />
              </template>
              {{ t('app.rename') }}
            </SButton>

            <SButton
              v-if="isOwner"
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
            v-if="isDeleted && isOwner"
            variant="primary"
            @click="lifecycle.restoreEntity(project.id)"
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
            :maxlength="INPUT_LIMITS.NAME"
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
          date: formatDateTime(project.deleted_at),
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
                  @click="lifecycle.copyToClipboard(project.id)"
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
        v-if="isOwner && !isDeleted"
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
            @click="lifecycle.deleteEntity(project.id)"
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
@import '../styles/detail-cards.css';

.settings-card {
  margin-bottom: 24px;
}

.owner-cell {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.owner-icon {
  color: var(--color-muted);
}
</style>
