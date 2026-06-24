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
import { useSessionStore } from '@shared/stores/session'
import { isProblemWithType } from '@shared/transport'
import {
  PencilIcon, UserGroupIcon, ArrowsRightLeftIcon,
  ClipboardIcon, TrashIcon, ArrowPathIcon,
} from '@heroicons/vue/24/outline'
import { orgsApi, type OrgMember } from '../api/orgs'
import { tenancyKeys } from '../queries'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const toast = useToast()
const { confirm, prompt } = useConfirmDialog()
const session = useSessionStore()
const qc = useQueryClient()

const orgId = computed(() => route.params.id as string)

const { data: org, isLoading, isError, refetch } = useQuery({
  queryKey: computed(() => tenancyKeys.org(orgId.value)),
  queryFn: () => orgsApi.get(orgId.value).then(r => r.data),
})

const { data: quotas } = useQuery({
  queryKey: computed(() => tenancyKeys.orgQuotas(orgId.value)),
  queryFn: () => orgsApi.quotas(orgId.value).then(r => r.data),
  retry: false,
})

const { data: members } = useQuery({
  queryKey: computed(() => tenancyKeys.orgMembers(orgId.value)),
  queryFn: () => orgsApi.listMembers(orgId.value).then(r => r.data),
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
      const { data } = await orgsApi.rename(org.value.id, name, org.value.version)
      qc.setQueryData(tenancyKeys.org(orgId.value), data)
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

async function deleteOrg(): Promise<void> {
  if (!org.value) return
  const name = await prompt({
    title: t('tenancy.org.deleteTitle'),
    message: t('tenancy.org.deleteBody'),
    variant: 'error',
    confirmLabel: t('tenancy.org.deleteConfirm'),
    inputPattern: new RegExp(`^${org.value.name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`),
    inputErrorMessage: t('tenancy.org.deleteConfirm'),
  })
  if (name === null) return
  try {
    await orgsApi.remove(org.value.id)
    toast.success(t('tenancy.org.deleted'))
    router.push({ name: 'tenancy.orgList' })
  } catch {
    toast.error(t('tenancy.org.loadError'))
  }
}

async function restoreOrg(): Promise<void> {
  if (!org.value) return
  const ok = await confirm({
    title: t('tenancy.org.restoreTitle'),
    message: t('tenancy.org.restoreBody'),
    variant: 'info',
    confirmLabel: t('tenancy.org.restoreConfirm'),
  })
  if (!ok) return
  try {
    await orgsApi.restore(org.value.id)
    qc.invalidateQueries({ queryKey: tenancyKeys.org(orgId.value) })
    toast.success(t('tenancy.org.restored'))
  } catch {
    toast.error(t('tenancy.org.loadError'))
  }
}

function copyId(): void {
  if (org.value) {
    navigator.clipboard.writeText(org.value.id)
  }
}

function formatDateTime(d: string | undefined | null): string {
  if (!d) return ''
  return d.replace('T', ' ').slice(0, 16)
}

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
    <SLoadingSpinner
      v-if="isLoading"
      :text="t('tenancy.common.loading')"
    />

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
          {{ t('app.confirm') }}
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
            <template v-if="!rename.renaming.value">
              <SButton
                v-if="isOwner"
                variant="ghost"
                size="sm"
                @click="rename.start"
              >
                <template #icon-left>
                  <PencilIcon class="w-4 h-4" />
                </template>
                {{ t('app.save') }}
              </SButton>
            </template>

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
            @click="restoreOrg"
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
          date: formatDateTime(org.deleted_at!),
          permanentDate: '—',
        }) }}
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
                    @click="copyId"
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
            @click="deleteOrg"
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

.detail-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
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

.quotas-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.quota-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.quota-label {
  font-size: 0.75rem;
  color: var(--color-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.quota-value {
  font-size: 1.25rem;
  font-weight: 600;
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

@media (max-width: 768px) {
  .detail-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 480px) {
  .quotas-grid {
    grid-template-columns: 1fr;
  }

  .danger-content {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
