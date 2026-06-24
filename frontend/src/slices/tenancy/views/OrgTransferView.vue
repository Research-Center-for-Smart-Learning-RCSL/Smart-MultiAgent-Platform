<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  SPageHeader, SCard, SAlert, SBadge, SButton, SFormField,
  SInput, SLoadingSpinner,
} from '@shared/ui'
import { useConfirmDialog, useToast } from '@shared/composables'
import { useSessionStore } from '@shared/stores/session'
import { isProblemWithType } from '@shared/transport'
import { orgsApi, type OriginalCreatorTransfer } from '../api/orgs'
import { tenancyKeys } from '../queries'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const toast = useToast()
const { confirm } = useConfirmDialog()
const session = useSessionStore()
const qc = useQueryClient()

const orgId = computed(() => route.params.id as string)

const { data: org } = useQuery({
  queryKey: computed(() => tenancyKeys.org(orgId.value)),
  queryFn: () => orgsApi.get(orgId.value).then(r => r.data),
})

const { data: transfers, isLoading, isError, refetch } = useQuery({
  queryKey: computed(() => tenancyKeys.orgTransfers(orgId.value)),
  queryFn: () => orgsApi.listTransfers(orgId.value).then(r => r.data),
})

const pending = computed<OriginalCreatorTransfer | null>(() =>
  transfers.value?.find(tr => tr.state === 'pending') ?? null,
)

const isInitiator = computed(() =>
  pending.value !== null && session.me?.id === pending.value.initiator_user_id,
)

const isTarget = computed(() =>
  pending.value !== null && session.me?.id === pending.value.target_user_id,
)

const targetUserId = ref('')
const fieldError = ref<string | null>(null)
const submitting = ref(false)

function formatDateTime(d: string): string {
  return d.replace('T', ' ').slice(0, 16)
}

async function initiate(): Promise<void> {
  const trimmed = targetUserId.value.trim()
  if (!trimmed || submitting.value) return

  const ok = await confirm({
    title: t('tenancy.transfer.initiateTitle'),
    message: t('tenancy.transfer.initiateBody'),
    variant: 'warning',
    confirmLabel: t('tenancy.transfer.initiateConfirm'),
  })
  if (!ok) return

  submitting.value = true
  fieldError.value = null
  try {
    await orgsApi.initiateTransfer(orgId.value, trimmed)
    targetUserId.value = ''
    qc.invalidateQueries({ queryKey: tenancyKeys.orgTransfers(orgId.value) })
    toast.success(t('tenancy.transfer.initiated'))
  } catch (e: unknown) {
    if (isProblemWithType(e, '/tenancy/transfer-conflict')) {
      toast.error(t('tenancy.transfer.alreadyPending'))
    } else if (isProblemWithType(e, '/tenancy/member-not-found')) {
      fieldError.value = t('tenancy.transfer.targetNotOwner')
    } else {
      toast.error(t('tenancy.transfer.loadError'))
    }
  } finally {
    submitting.value = false
  }
}

async function cancelTransfer(): Promise<void> {
  if (!pending.value) return
  const ok = await confirm({
    title: t('tenancy.transfer.cancelTitle'),
    message: t('tenancy.transfer.cancelConfirm'),
    variant: 'error',
    confirmLabel: t('tenancy.transfer.cancelConfirm'),
  })
  if (!ok) return
  try {
    await orgsApi.cancelTransfer(orgId.value, pending.value.id)
    qc.invalidateQueries({ queryKey: tenancyKeys.orgTransfers(orgId.value) })
    toast.success(t('tenancy.transfer.cancelled'))
  } catch {
    toast.error(t('tenancy.transfer.loadError'))
  }
}

async function acceptTransfer(): Promise<void> {
  if (!pending.value) return
  const ok = await confirm({
    title: t('tenancy.transfer.acceptTitle'),
    message: t('tenancy.transfer.acceptBody'),
    variant: 'warning',
    confirmLabel: t('tenancy.transfer.acceptLabel'),
  })
  if (!ok) return
  try {
    await orgsApi.acceptTransfer(orgId.value, pending.value.id)
    qc.invalidateQueries({ queryKey: tenancyKeys.orgTransfers(orgId.value) })
    toast.success(t('tenancy.transfer.accepted'))
    router.push({ name: 'tenancy.orgDetail', params: { id: orgId.value } })
  } catch (e: unknown) {
    if (isProblemWithType(e, '/tenancy/transfer-conflict')) {
      toast.error(t('tenancy.transfer.alreadyPending'))
    } else {
      toast.error(t('tenancy.transfer.loadError'))
    }
  }
}

async function decline(): Promise<void> {
  if (!pending.value) return
  try {
    await orgsApi.cancelTransfer(orgId.value, pending.value.id)
    qc.invalidateQueries({ queryKey: tenancyKeys.orgTransfers(orgId.value) })
    toast.success(t('tenancy.transfer.cancelled'))
  } catch {
    toast.error(t('tenancy.transfer.loadError'))
  }
}

const breadcrumbs = computed(() => [
  { label: t('tenancy.breadcrumb.home'), to: { name: 'tenancy.orgList' } },
  { label: t('tenancy.breadcrumb.organizations'), to: { name: 'tenancy.orgList' } },
  { label: org.value?.name ?? '...', to: { name: 'tenancy.orgDetail', params: { id: orgId.value } } },
  { label: t('tenancy.breadcrumb.transfer') },
])
</script>

<template>
  <div>
    <SPageHeader
      :title="t('tenancy.transfer.pageTitle')"
      :breadcrumbs="breadcrumbs"
    />

    <SLoadingSpinner
      v-if="isLoading"
      :text="t('tenancy.common.loading')"
    />

    <SAlert
      v-else-if="isError"
      variant="danger"
    >
      {{ t('tenancy.transfer.loadError') }}
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

    <template v-else>
      <!-- No pending transfer: show initiate form (OC only) -->
      <template v-if="!pending">
        <SAlert
          variant="info"
          class="transfer-info"
        >
          {{ t('tenancy.transfer.infoText') }}
        </SAlert>

        <SCard
          variant="bordered"
          class="transfer-card"
        >
          <form @submit.prevent="initiate">
            <SFormField
              :label="t('tenancy.transfer.targetLabel')"
              name="targetUserId"
              :error="fieldError ?? undefined"
              :help="t('tenancy.transfer.targetHelp')"
              required
            >
              <SInput
                v-model="targetUserId"
                :error="!!fieldError"
                :disabled="submitting"
              />
            </SFormField>

            <div class="transfer-actions">
              <SButton
                type="submit"
                variant="primary"
                :loading="submitting"
                :disabled="submitting || !targetUserId.trim()"
              >
                {{ t('tenancy.transfer.initiateConfirm') }}
              </SButton>
            </div>
          </form>
        </SCard>
      </template>

      <!-- Pending transfer: initiator view -->
      <SCard
        v-else-if="isInitiator"
        variant="bordered"
        class="transfer-card"
      >
        <div class="pending-header">
          <SBadge variant="warning">
            {{ t('tenancy.transfer.pendingLabel') }}
          </SBadge>
        </div>

        <dl class="transfer-details">
          <div class="detail-row">
            <dt>{{ t('tenancy.transfer.targetField') }}</dt>
            <dd>{{ pending.target_user_id }}</dd>
          </div>
          <div class="detail-row">
            <dt>{{ t('tenancy.transfer.initiatedField') }}</dt>
            <dd>{{ formatDateTime(pending.created_at) }}</dd>
          </div>
          <div class="detail-row">
            <dt>{{ t('tenancy.transfer.expiresField') }}</dt>
            <dd>{{ formatDateTime(pending.expires_at) }}</dd>
          </div>
        </dl>

        <div class="transfer-actions">
          <SButton
            variant="danger"
            @click="cancelTransfer"
          >
            {{ t('tenancy.transfer.cancelLabel') }}
          </SButton>
        </div>
      </SCard>

      <!-- Pending transfer: target view -->
      <SCard
        v-else-if="isTarget"
        variant="bordered"
        class="transfer-card"
      >
        <SAlert
          variant="info"
          class="target-notice"
        >
          {{ t('tenancy.transfer.targetNotice') }}
        </SAlert>

        <dl class="transfer-details">
          <div class="detail-row">
            <dt>{{ t('tenancy.transfer.initiatedByField') }}</dt>
            <dd>{{ pending.initiator_user_id }}</dd>
          </div>
          <div class="detail-row">
            <dt>{{ t('tenancy.transfer.expiresField') }}</dt>
            <dd>{{ formatDateTime(pending.expires_at) }}</dd>
          </div>
        </dl>

        <div class="transfer-actions target-actions">
          <SButton
            variant="secondary"
            @click="decline"
          >
            {{ t('tenancy.transfer.declineLabel') }}
          </SButton>
          <SButton
            variant="primary"
            @click="acceptTransfer"
          >
            {{ t('tenancy.transfer.acceptLabel') }}
          </SButton>
        </div>
      </SCard>

      <!-- Pending transfer: non-target, non-initiator (read only) -->
      <SCard
        v-else-if="pending"
        variant="bordered"
        class="transfer-card"
      >
        <div class="pending-header">
          <SBadge variant="warning">
            {{ t('tenancy.transfer.pendingLabel') }}
          </SBadge>
        </div>

        <dl class="transfer-details">
          <div class="detail-row">
            <dt>{{ t('tenancy.transfer.targetField') }}</dt>
            <dd>{{ pending.target_user_id }}</dd>
          </div>
          <div class="detail-row">
            <dt>{{ t('tenancy.transfer.expiresField') }}</dt>
            <dd>{{ formatDateTime(pending.expires_at) }}</dd>
          </div>
        </dl>
      </SCard>
    </template>
  </div>
</template>

<style scoped>
.transfer-info {
  margin-bottom: 24px;
}

.transfer-card {
  max-width: 600px;
}

.pending-header {
  margin-bottom: 16px;
}

.transfer-details {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-bottom: 24px;
}

.detail-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.detail-row dt {
  font-size: 0.875rem;
  color: var(--color-muted);
}

.detail-row dd {
  font-size: 0.875rem;
}

.transfer-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
}

.target-notice {
  margin-bottom: 16px;
}

.target-actions {
  justify-content: flex-end;
}

@media (max-width: 768px) {
  .transfer-card {
    max-width: none;
  }
}

@media (max-width: 480px) {
  .target-actions {
    flex-direction: column;
  }

  .target-actions :deep(.s-button) {
    width: 100%;
  }
}
</style>
