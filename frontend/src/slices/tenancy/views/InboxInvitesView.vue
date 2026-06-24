<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  SPageHeader, SCard, SButton, SBadge, SAlert,
  SEmptyState, SSkeleton,
} from '@shared/ui'
import { useConfirmDialog, useToast } from '@shared/composables'
import { useSessionStore } from '@shared/stores/session'
import { isProblemWithType } from '@shared/transport'
import {
  BuildingOffice2Icon, FolderIcon, InboxArrowDownIcon,
} from '@heroicons/vue/24/outline'
import { invitesApi, type Invite } from '../api/invites'
import { tenancyKeys } from '../queries'
import { formatDate } from '../utils/formatters'

const { t } = useI18n()
const toast = useToast()
const { confirm } = useConfirmDialog()
const session = useSessionStore()
const qc = useQueryClient()

const { data: invites, isLoading, isError, refetch } = useQuery({
  queryKey: tenancyKeys.invites('pending'),
  queryFn: () => invitesApi.list('pending').then(r => r.data),
})

function scopeIcon(invite: Invite) {
  return invite.scope_type === 'org' ? BuildingOffice2Icon : FolderIcon
}

function scopeLabel(invite: Invite): string {
  return invite.scope_type === 'org'
    ? t('tenancy.invite.scopeOrg')
    : t('tenancy.invite.scopeProject')
}

function roleBadgeVariant(invite: Invite): 'info' | 'neutral' {
  return invite.role === 'owner' ? 'info' : 'neutral'
}

function inviteRoleLabel(invite: Invite): string {
  return invite.role === 'owner' ? t('tenancy.role.owner') : t('tenancy.role.member')
}

function expiryClass(invite: Invite): string {
  const diff = new Date(invite.expires_at).getTime() - Date.now()
  const hours = diff / 3600000
  if (hours <= 24) return 'expiry-danger'
  if (hours <= 48) return 'expiry-warning'
  return ''
}

async function accept(invite: Invite): Promise<void> {
  try {
    await invitesApi.accept(invite.id)
    qc.invalidateQueries({ queryKey: tenancyKeys.invites('pending') })
    toast.success(t('tenancy.invite.accepted', { name: invite.scope_name }))
  } catch (e: unknown) {
    if (isProblemWithType(e, '/auth/email-unverified')) {
      toast.error(t('tenancy.invite.unverifiedWarning'))
    } else if (isProblemWithType(e, '/tenancy/invite-expired')) {
      qc.invalidateQueries({ queryKey: tenancyKeys.invites('pending') })
      toast.warning(t('tenancy.invite.expired'))
    } else {
      toast.error(t('tenancy.invite.acceptError'))
    }
  }
}

async function reject(invite: Invite): Promise<void> {
  const ok = await confirm({
    title: t('tenancy.invite.rejectTitle'),
    message: t('tenancy.invite.rejectBody', { name: invite.scope_name }),
    variant: 'warning',
    confirmLabel: t('tenancy.invite.rejectConfirm'),
  })
  if (!ok) return
  try {
    await invitesApi.reject(invite.id)
    qc.invalidateQueries({ queryKey: tenancyKeys.invites('pending') })
    toast.success(t('tenancy.invite.rejected'))
  } catch {
    toast.error(t('tenancy.invite.acceptError'))
  }
}

const breadcrumbs = computed(() => [
  { label: t('tenancy.breadcrumb.home'), to: { name: 'tenancy.orgList' } },
])
</script>

<template>
  <div>
    <SPageHeader
      :title="t('tenancy.invite.inboxTitle')"
      :breadcrumbs="breadcrumbs"
    />

    <!-- Email verification warning -->
    <SAlert
      v-if="!session.isVerified"
      variant="warning"
      class="verify-alert"
    >
      {{ t('tenancy.invite.unverifiedWarning') }}
      <template #actions>
        <SButton
          variant="secondary"
          size="sm"
          as="router-link"
          :to="{ name: 'identity.verifyEmail' }"
        >
          {{ t('tenancy.invite.verifyNow') }}
        </SButton>
      </template>
    </SAlert>

    <!-- Error state -->
    <SAlert
      v-if="isError"
      variant="danger"
    >
      {{ t('tenancy.invite.loadError') }}
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

    <!-- Loading state -->
    <div
      v-else-if="isLoading"
      class="skeleton-stack"
    >
      <SSkeleton
        v-for="i in 3"
        :key="i"
        variant="rect"
        height="120px"
      />
    </div>

    <!-- Empty state -->
    <SEmptyState
      v-else-if="!invites?.length"
      :icon="InboxArrowDownIcon"
      :title="t('tenancy.invite.empty')"
    />

    <!-- Invite cards -->
    <div
      v-else
      class="invite-list"
    >
      <SCard
        v-for="invite in invites"
        :key="invite.id"
        variant="bordered"
        class="invite-card"
      >
        <div class="invite-header">
          <component
            :is="scopeIcon(invite)"
            class="w-5 h-5 scope-icon"
          />
          <div class="invite-title-block">
            <span class="invite-title">{{ invite.scope_name }}</span>
            <span class="invite-scope">{{ scopeLabel(invite) }}</span>
          </div>
        </div>

        <div class="invite-meta">
          <div class="invite-role-row">
            <span class="meta-label">{{ t('tenancy.invite.invitedAs') }}:</span>
            <SBadge :variant="roleBadgeVariant(invite)">
              {{ inviteRoleLabel(invite) }}
            </SBadge>
          </div>
          <div class="invite-expiry-row">
            <span class="meta-label">{{ t('tenancy.invite.expires') }}:</span>
            <span :class="expiryClass(invite)">
              {{ formatDate(invite.expires_at) }}
            </span>
          </div>
        </div>

        <div class="invite-actions">
          <SButton
            variant="secondary"
            size="sm"
            @click="reject(invite)"
          >
            {{ t('tenancy.invite.rejectConfirm') }}
          </SButton>
          <SButton
            variant="primary"
            size="sm"
            @click="accept(invite)"
          >
            {{ t('tenancy.invite.acceptButton') }}
          </SButton>
        </div>
      </SCard>
    </div>
  </div>
</template>

<style scoped>
.verify-alert {
  margin-bottom: 24px;
}

.skeleton-stack {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-width: 640px;
}

.invite-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-width: 640px;
}

.invite-card {
  padding: 20px;
}

.invite-header {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 16px;
}

.scope-icon {
  color: var(--color-accent);
  flex-shrink: 0;
  margin-top: 2px;
}

.invite-title-block {
  display: flex;
  flex-direction: column;
}

.invite-title {
  font-size: 1rem;
  font-weight: 600;
}

.invite-scope {
  font-size: 0.75rem;
  color: var(--color-muted);
}

.invite-meta {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 16px;
}

.invite-role-row,
.invite-expiry-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.meta-label {
  font-size: 0.875rem;
  color: var(--color-muted);
}

.expiry-warning {
  color: var(--color-warning);
}

.expiry-danger {
  color: var(--color-danger);
}

.invite-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

@media (max-width: 768px) {
  .invite-list {
    max-width: none;
  }
}

@media (max-width: 480px) {
  .invite-actions {
    flex-direction: column;
  }

  .invite-actions :deep(.s-button) {
    width: 100%;
  }

  .scope-icon {
    display: none;
  }
}
</style>
