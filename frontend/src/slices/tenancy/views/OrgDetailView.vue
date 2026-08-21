<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  SPageHeader, SCard, SButton, SInput, SBadge, SAlert,
  SLoadingSpinner, SSkeleton, STooltip,
} from '@shared/ui'
import { useInlineRename, useToast } from '@shared/composables'
import { useSessionStore } from '@shared/stores/session'
import { isProblemWithType } from '@shared/transport'
import { INPUT_LIMITS } from '@shared/constants/inputLimits'
import {
  PencilIcon, UserGroupIcon, ArrowsRightLeftIcon,
  ClipboardIcon, TrashIcon, ArrowPathIcon, SparklesIcon, PuzzlePieceIcon,
} from '@heroicons/vue/24/outline'
import { orgsApi, type OrgMember } from '../api/orgs'
import { tenancyKeys } from '../queries'
import { formatDateTime } from '../utils/formatters'
import { useEntityLifecycle } from '../composables/useEntityLifecycle'

const { t } = useI18n()
const route = useRoute()
const toast = useToast()
const session = useSessionStore()
const qc = useQueryClient()

const orgId = computed(() => route.params.id as string)

const { data: org, isLoading, isError, refetch } = useQuery({
  queryKey: computed(() => tenancyKeys.org(orgId.value)),
  queryFn: () => orgsApi.get(orgId.value),
})

const { data: quotas } = useQuery({
  queryKey: computed(() => tenancyKeys.orgQuotas(orgId.value)),
  queryFn: () => orgsApi.quotas(orgId.value),
  retry: false,
})

const { data: members, isError: membersError } = useQuery({
  queryKey: computed(() => tenancyKeys.orgMembers(orgId.value)),
  queryFn: () => orgsApi.listMembers(orgId.value),
})

const myMembership = computed<OrgMember | null>(() => {
  if (!members.value || !session.me) return null
  return members.value.find(m => m.user_id === session.me!.id) ?? null
})

const isOC = computed(() => myMembership.value?.is_original_creator === true)
const isOwner = computed(() => isOC.value || myMembership.value?.role === 'owner')
const isDeleted = computed(() => !!org.value?.deleted_at)

const rename = useInlineRename({
  current: () => org.value?.name ?? '',
  save: async (name) => {
    if (!org.value) return
    try {
      const renamed = await orgsApi.rename(org.value.id, name, org.value.version)
      qc.setQueryData(tenancyKeys.org(orgId.value), renamed)
    } catch (e: unknown) {
      if (isProblemWithType(e, '/tenancy/name-taken')) {
        toast.error(t('tenancy.org.nameTaken'))
      } else if (isProblemWithType(e, '/tenancy/version-mismatch')) {
        toast.warning(t('tenancy.common.versionConflict'))
        refetch()
      } else {
        toast.error(t('tenancy.org.loadError'))
      }
      throw e
    }
  },
})

const lifecycle = useEntityLifecycle({
  entityName: () => org.value?.name ?? '',
  deleteTitle: () => t('tenancy.org.deleteTitle'),
  deleteBody: () => t('tenancy.org.deleteBody'),
  deleteConfirmLabel: () => t('tenancy.org.deleteConfirm'),
  deletedToast: () => t('tenancy.org.deleted'),
  restoreTitle: () => t('tenancy.org.restoreTitle'),
  restoreBody: () => t('tenancy.org.restoreBody'),
  restoreConfirmLabel: () => t('tenancy.org.restoreConfirm'),
  restoredToast: () => t('tenancy.org.restored'),
  errorToast: () => t('tenancy.org.loadError'),
  removeApi: (id) => orgsApi.remove(id),
  restoreApi: (id) => orgsApi.restore(id),
  queryKey: () => tenancyKeys.org(orgId.value),
  qc,
  listRoute: 'tenancy.orgList',
})

function quotaColor(count: number, target: number): string {
  const pct = count / target
  if (pct >= 0.95) return 'var(--color-danger)'
  if (pct >= 0.8) return 'var(--color-warning)'
  return 'var(--color-fg)'
}

const breadcrumbs = computed(() => [
  { label: t('tenancy.breadcrumb.home'), to: { name: 'tenancy.orgList' } },
  { label: t('tenancy.breadcrumb.organizations'), to: { name: 'tenancy.orgList' } },
  { label: org.value?.name ?? '...' },
])
</script>

<template>
  <div>
    <!-- A header-shaped skeleton during the fetch, so a cold load reads as this
         page loading rather than as a blank content area with a 24px spinner
         row in the corner. Deliberately NOT an SPageHeader titled "Loading":
         the title lands in an <h1>, which would make a status string the
         page's heading and its accessible name for the whole fetch, and would
         break the invariant that an h1 on a detail page IS the entity's name.
         12-shared-patterns.md §5.1 asks for a structural skeleton here anyway. -->
    <template v-if="isLoading">
      <div class="mb-4">
        <SSkeleton
          width="200px"
          height="2rem"
        />
      </div>
      <div class="flex justify-center py-16">
        <SLoadingSpinner :label="t('tenancy.common.loading')" />
      </div>
    </template>

    <SAlert
      v-else-if="isError"
      variant="danger"
    >
      {{ t('tenancy.org.loadError') }}
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

    <template v-else-if="org">
      <SPageHeader
        :title="org.name"
        :breadcrumbs="breadcrumbs"
      >
        <template #prepend>
          <SBadge
            v-if="isDeleted"
            variant="danger"
          >
            {{ t('tenancy.org.deleteConfirm') }}
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
              variant="secondary"
              as="router-link"
              :to="{ name: 'tenancy.orgMembers', params: { id: org.id } }"
            >
              <template #icon-left>
                <UserGroupIcon class="w-4 h-4" />
              </template>
              {{ t('tenancy.breadcrumb.members') }}
            </SButton>

            <SButton
              v-if="isOwner"
              variant="secondary"
              as="router-link"
              :to="{ name: 'prompt-studio.org', params: { orgId: org.id } }"
            >
              <template #icon-left>
                <SparklesIcon class="w-4 h-4" />
              </template>
              {{ t('tenancy.org.promptAssistant') }}
            </SButton>

            <SButton
              v-if="isOwner"
              variant="secondary"
              as="router-link"
              :to="{ name: 'skills.org', params: { orgId: org.id } }"
            >
              <template #icon-left>
                <PuzzlePieceIcon class="w-4 h-4" />
              </template>
              {{ t('tenancy.org.skills') }}
            </SButton>

            <SButton
              v-if="isOC"
              variant="secondary"
              as="router-link"
              :to="{ name: 'tenancy.orgTransfer', params: { id: org.id } }"
            >
              <template #icon-left>
                <ArrowsRightLeftIcon class="w-4 h-4" />
              </template>
              {{ t('tenancy.breadcrumb.transfer') }}
            </SButton>
          </template>

          <SButton
            v-if="isDeleted && isOC"
            variant="primary"
            @click="lifecycle.restoreEntity(org.id)"
          >
            <template #icon-left>
              <ArrowPathIcon class="w-4 h-4" />
            </template>
            {{ t('tenancy.org.restoreConfirm') }}
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
        {{ t('tenancy.org.deletedBanner', {
          date: formatDateTime(org.deleted_at),
          permanentDate: '—',
        }) }}
      </SAlert>

      <!-- Members query error -->
      <SAlert
        v-if="membersError"
        variant="warning"
      >
        {{ t('tenancy.org.loadError') }}
      </SAlert>

      <div class="detail-grid">
        <!-- Settings card -->
        <SCard variant="bordered">
          <h2 class="card-title">
            {{ t('tenancy.settings.title') }}
          </h2>
          <dl class="settings-list">
            <div class="settings-row">
              <dt>{{ t('tenancy.settings.orgId') }}</dt>
              <dd class="mono-value">
                {{ org.id.slice(0, 8) }}...
                <STooltip :content="org.id">
                  <button
                    class="copy-btn"
                    @click="lifecycle.copyToClipboard(org.id)"
                  >
                    <ClipboardIcon class="w-4 h-4" />
                  </button>
                </STooltip>
              </dd>
            </div>
            <div class="settings-row">
              <dt>{{ t('tenancy.settings.created') }}</dt>
              <dd>{{ formatDateTime(org.created_at) }}</dd>
            </div>
            <div class="settings-row">
              <dt>{{ t('tenancy.settings.version') }}</dt>
              <dd>{{ org.version }}</dd>
            </div>
            <div
              v-if="org.default_project_id"
              class="settings-row"
            >
              <dt>{{ t('tenancy.settings.defaultProject') }}</dt>
              <dd>
                <router-link :to="{ name: 'tenancy.projectDetail', params: { id: org.default_project_id } }">
                  {{ org.default_project_id.slice(0, 8) }}...
                </router-link>
              </dd>
            </div>
          </dl>
        </SCard>

        <!-- Quotas card -->
        <SCard
          v-if="quotas"
          variant="bordered"
        >
          <h2 class="card-title">
            {{ t('tenancy.quotas.title') }}
          </h2>
          <div class="quotas-grid">
            <div class="quota-item">
              <span class="quota-label">{{ t('tenancy.quotas.members') }}</span>
              <span
                class="quota-value"
                :style="{ color: quotaColor(quotas.users, quotas.advisory_targets.users ?? Infinity) }"
              >
                {{ quotas.users }} / {{ quotas.advisory_targets.users ?? '—' }}
              </span>
            </div>
            <div class="quota-item">
              <span class="quota-label">{{ t('tenancy.quotas.projects') }}</span>
              <span
                class="quota-value"
                :style="{ color: quotaColor(quotas.projects, quotas.advisory_targets.projects ?? Infinity) }"
              >
                {{ quotas.projects }} / {{ quotas.advisory_targets.projects ?? '—' }}
              </span>
            </div>
            <div class="quota-item">
              <span class="quota-label">{{ t('tenancy.quotas.chatrooms') }}</span>
              <span
                class="quota-value"
                :style="{ color: quotaColor(quotas.chatrooms, quotas.advisory_targets.chatrooms ?? Infinity) }"
              >
                {{ quotas.chatrooms }} / {{ quotas.advisory_targets.chatrooms ?? '—' }}
              </span>
            </div>
            <div class="quota-item">
              <span class="quota-label">{{ t('tenancy.quotas.agents') }}</span>
              <span
                class="quota-value"
                :style="{ color: quotaColor(quotas.agents, quotas.advisory_targets.agents ?? Infinity) }"
              >
                {{ quotas.agents }} / {{ quotas.advisory_targets.agents ?? '—' }}
              </span>
            </div>
            <div class="quota-item">
              <span class="quota-label">{{ t('tenancy.quotas.workflows') }}</span>
              <span
                class="quota-value"
                :style="{ color: quotaColor(quotas.workflows, quotas.advisory_targets.workflows ?? Infinity) }"
              >
                {{ quotas.workflows }} / {{ quotas.advisory_targets.workflows ?? '—' }}
              </span>
            </div>
          </div>
        </SCard>
      </div>

      <!-- Danger zone -->
      <SCard
        v-if="isOC && !isDeleted"
        variant="bordered"
        class="danger-zone"
      >
        <h2 class="danger-title">
          {{ t('tenancy.dangerZone.title') }}
        </h2>
        <div class="danger-content">
          <div>
            <p class="danger-heading">
              {{ t('tenancy.org.deleteTitle') }}
            </p>
            <p class="danger-description">
              {{ t('tenancy.dangerZone.deleteOrgDescription') }}
            </p>
          </div>
          <SButton
            variant="danger"
            @click="lifecycle.deleteEntity(org.id)"
          >
            <template #icon-left>
              <TrashIcon class="w-4 h-4" />
            </template>
            {{ t('tenancy.org.deleteConfirm') }}
          </SButton>
        </div>
      </SCard>
    </template>
  </div>
</template>

<style scoped>
@import '../styles/detail-cards.css';

.detail-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-6);
  margin-bottom: var(--space-6);
}

.quotas-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-4);
}

.quota-item {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.quota-label {
  font-size: var(--font-size-xs);
  color: var(--color-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.quota-value {
  font-size: var(--font-size-xl);
  font-weight: var(--weight-semibold);
}

@media (max-width: 767px) {
  .detail-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 479px) {
  .quotas-grid {
    grid-template-columns: 1fr;
  }
}
</style>
