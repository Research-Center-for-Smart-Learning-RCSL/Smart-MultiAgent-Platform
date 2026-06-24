<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  SPageHeader, SCard, STable, SAvatar, SBadge, SButton,
  SFormField, SInput, SSelect, SDropdown, SAlert, SEmptyState,
} from '@shared/ui'
import { useConfirmDialog, useToast } from '@shared/composables'
import { useSessionStore } from '@shared/stores/session'
import { isProblemWithType } from '@shared/transport'
import { UserGroupIcon, EllipsisVerticalIcon } from '@heroicons/vue/24/outline'
import { orgsApi, type OrgMember } from '../api/orgs'
import { tenancyKeys } from '../queries'

const { t } = useI18n()
const route = useRoute()
const toast = useToast()
const { confirm } = useConfirmDialog()
const session = useSessionStore()
const qc = useQueryClient()

const orgId = computed(() => route.params.id as string)

const { data: org } = useQuery({
  queryKey: computed(() => tenancyKeys.org(orgId.value)),
  queryFn: () => orgsApi.get(orgId.value).then(r => r.data),
})

const { data: members, isLoading, isError, refetch } = useQuery({
  queryKey: computed(() => tenancyKeys.orgMembers(orgId.value)),
  queryFn: () => orgsApi.listMembers(orgId.value).then(r => r.data),
})

const myMembership = computed<OrgMember | null>(() => {
  if (!members.value || !session.me) return null
  return members.value.find(m => m.user_id === session.me!.id) ?? null
})

const isOC = computed(() => myMembership.value?.is_original_creator === true)
const isOwnerOrOC = computed(() => isOC.value || myMembership.value?.role === 'owner')

const inviteEmail = ref('')
const inviteRole = ref<'owner' | 'member'>('member')
const invitePending = ref(false)
const inviteError = ref<string | null>(null)

const roleOptions = [
  { value: 'member', label: t('tenancy.role.member') },
  { value: 'owner', label: t('tenancy.role.owner') },
]

const columns = computed(() => [
  { key: 'avatar', label: '', width: '48px' },
  { key: 'email', label: 'Email', sortable: true },
  { key: 'role', label: t('tenancy.role.member'), sortable: true, width: '140px' },
  { key: 'joined_at', label: t('tenancy.settings.created'), sortable: true, width: '120px' },
  { key: 'actions', label: '', width: '48px' },
])

function roleBadgeVariant(member: OrgMember): 'info' | 'neutral' {
  return member.is_original_creator ? 'info' : 'neutral'
}

function roleLabel(member: OrgMember): string {
  if (member.is_original_creator) return t('tenancy.role.originalCreator')
  return member.role === 'owner' ? t('tenancy.role.owner') : t('tenancy.role.member')
}

function formatRelative(d: string): string {
  const diff = Date.now() - new Date(d).getTime()
  const days = Math.floor(diff / 86400000)
  if (days < 1) return t('tenancy.settings.created')
  if (days < 30) return `${days}d`
  const months = Math.floor(days / 30)
  return `${months}mo`
}

function isMe(member: OrgMember): boolean {
  return session.me?.id === member.user_id
}

function getRowActions(member: OrgMember): Array<{ key: string; label: string; danger?: boolean }> {
  if (member.is_original_creator || isMe(member)) return []
  const items: Array<{ key: string; label: string; danger?: boolean }> = []

  if (member.role === 'member' && isOwnerOrOC.value) {
    items.push({ key: 'promote', label: t('tenancy.role.owner') })
  }
  if (member.role === 'owner' && !member.is_original_creator && isOC.value) {
    items.push({ key: 'demote', label: t('tenancy.role.member') })
  }
  if (!member.is_original_creator && isOwnerOrOC.value) {
    items.push({ key: 'remove', label: t('tenancy.member.removeConfirm'), danger: true })
  }
  return items
}

async function onInvite(): Promise<void> {
  const email = inviteEmail.value.trim()
  if (!email) return
  invitePending.value = true
  inviteError.value = null
  try {
    await orgsApi.invite(orgId.value, email, inviteRole.value)
    inviteEmail.value = ''
    inviteRole.value = 'member'
    qc.invalidateQueries({ queryKey: tenancyKeys.orgMembers(orgId.value) })
    toast.success(t('tenancy.member.invited', { email }))
  } catch (e: unknown) {
    if (isProblemWithType(e, '/tenancy/invite-duplicate')) {
      inviteError.value = t('tenancy.member.alreadyInvited')
    } else {
      toast.error(t('tenancy.member.rateLimited'))
    }
  } finally {
    invitePending.value = false
  }
}

async function onAction(key: string, member: OrgMember): Promise<void> {
  if (key === 'promote') {
    const ok = await confirm({
      title: t('tenancy.member.changeRoleTitle'),
      message: t('tenancy.member.changeRoleBody', { email: member.email, role: t('tenancy.role.owner') }),
      variant: 'info',
    })
    if (!ok) return
    try {
      await orgsApi.setRole(orgId.value, member.user_id, 'owner')
      qc.invalidateQueries({ queryKey: tenancyKeys.orgMembers(orgId.value) })
      toast.success(t('tenancy.member.roleChanged'))
    } catch {
      toast.error(t('tenancy.org.loadError'))
    }
  } else if (key === 'demote') {
    const ok = await confirm({
      title: t('tenancy.member.changeRoleTitle'),
      message: t('tenancy.member.changeRoleBody', { email: member.email, role: t('tenancy.role.member') }),
      variant: 'info',
    })
    if (!ok) return
    try {
      await orgsApi.setRole(orgId.value, member.user_id, 'member')
      qc.invalidateQueries({ queryKey: tenancyKeys.orgMembers(orgId.value) })
      toast.success(t('tenancy.member.roleChanged'))
    } catch {
      toast.error(t('tenancy.org.loadError'))
    }
  } else if (key === 'remove') {
    const ok = await confirm({
      title: t('tenancy.member.removeTitle'),
      message: t('tenancy.member.removeBody'),
      variant: 'error',
      confirmLabel: t('tenancy.member.removeConfirm'),
    })
    if (!ok) return
    try {
      await orgsApi.removeMember(orgId.value, member.user_id)
      qc.invalidateQueries({ queryKey: tenancyKeys.orgMembers(orgId.value) })
      toast.success(t('tenancy.member.removed'))
    } catch (e: unknown) {
      if (isProblemWithType(e, '/tenancy/original-creator-conflict')) {
        toast.error(t('tenancy.member.cannotRemoveOC'))
      } else {
        toast.error(t('tenancy.org.loadError'))
      }
    }
  }
}

const breadcrumbs = computed(() => [
  { label: t('tenancy.breadcrumb.home'), to: { name: 'tenancy.orgList' } },
  { label: t('tenancy.breadcrumb.organizations'), to: { name: 'tenancy.orgList' } },
  { label: org.value?.name ?? '...', to: { name: 'tenancy.orgDetail', params: { id: orgId.value } } },
  { label: t('tenancy.breadcrumb.members') },
])
</script>

<template>
  <div>
    <SPageHeader
      :title="t('tenancy.breadcrumb.members')"
      :breadcrumbs="breadcrumbs"
    />

    <!-- Invite form -->
    <SCard
      v-if="isOwnerOrOC"
      variant="flat"
      class="invite-card"
    >
      <form
        class="invite-form"
        @submit.prevent="onInvite"
      >
        <SFormField
          :label="'Email'"
          name="inviteEmail"
          :error="inviteError ?? undefined"
          required
          class="invite-email"
        >
          <SInput
            v-model="inviteEmail"
            type="email"
            placeholder="user@example.com"
            :error="!!inviteError"
            :disabled="invitePending"
          />
        </SFormField>

        <SFormField
          :label="t('tenancy.role.member')"
          name="inviteRole"
          class="invite-role"
        >
          <SSelect
            v-model="inviteRole"
            :options="roleOptions"
            :disabled="invitePending"
          />
        </SFormField>

        <SButton
          type="submit"
          variant="primary"
          :loading="invitePending"
          :disabled="invitePending || !inviteEmail.trim()"
          class="invite-btn"
        >
          {{ t('tenancy.member.sendInvite') }}
        </SButton>
      </form>
    </SCard>

    <!-- Error state -->
    <SAlert
      v-if="isError"
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

    <!-- Member table -->
    <STable
      v-else
      :columns="columns"
      :data="members ?? []"
      :loading="isLoading"
      row-key="user_id"
    >
      <template #cell-avatar="{ row }">
        <SAvatar
          :name="row.email"
          size="sm"
        />
      </template>

      <template #cell-email="{ row }">
        {{ row.email }}
        <span
          v-if="isMe(row as OrgMember)"
          class="you-label"
        >
          {{ t('tenancy.member.you') }}
        </span>
      </template>

      <template #cell-role="{ row }">
        <SBadge :variant="roleBadgeVariant(row as OrgMember)">
          {{ roleLabel(row as OrgMember) }}
        </SBadge>
      </template>

      <template #cell-joined_at="{ row }">
        {{ formatRelative(row.joined_at) }}
      </template>

      <template #cell-actions="{ row }">
        <SDropdown
          v-if="getRowActions(row as OrgMember).length > 0"
          :items="getRowActions(row as OrgMember)"
          @select="(key: string) => onAction(key, row as OrgMember)"
        >
          <template #trigger>
            <SButton
              variant="ghost"
              size="sm"
              icon-only
            >
              <EllipsisVerticalIcon class="w-4 h-4" />
            </SButton>
          </template>
        </SDropdown>
      </template>

      <template #empty>
        <SEmptyState
          :icon="UserGroupIcon"
          :title="t('tenancy.member.empty')"
        />
      </template>
    </STable>
  </div>
</template>

<style scoped>
.invite-card {
  margin-bottom: 24px;
}

.invite-form {
  display: flex;
  align-items: flex-end;
  gap: 12px;
}

.invite-email {
  flex: 1;
}

.invite-role {
  width: 160px;
}

.invite-btn {
  flex-shrink: 0;
}

.you-label {
  color: var(--color-muted);
  font-size: 0.875rem;
  margin-left: 4px;
}

@media (max-width: 768px) {
  .invite-form {
    flex-direction: column;
    align-items: stretch;
  }

  .invite-role {
    width: 100%;
  }
}
</style>
