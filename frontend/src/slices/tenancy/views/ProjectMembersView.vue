<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  SPageHeader, SCard, STable, SAvatar, SBadge, SButton,
  SFormField, SInput, SSelect, SDropdown, SAlert, SEmptyState,
  STooltip,
} from '@shared/ui'
import { useConfirmDialog, useToast } from '@shared/composables'
import { useSessionStore } from '@shared/stores/session'
import { isProblemWithType } from '@shared/transport'
import { UserGroupIcon, EllipsisVerticalIcon } from '@heroicons/vue/24/outline'
import { projectsApi, type ProjectMember } from '../api/projects'
import { tenancyKeys } from '../queries'

const { t } = useI18n()
const route = useRoute()
const toast = useToast()
const { confirm } = useConfirmDialog()
const session = useSessionStore()
const qc = useQueryClient()

const projectId = computed(() => route.params.id as string)

const { data: project } = useQuery({
  queryKey: computed(() => tenancyKeys.project(projectId.value)),
  queryFn: () => projectsApi.get(projectId.value).then(r => r.data),
})

const { data: members, isLoading, isError, refetch } = useQuery({
  queryKey: computed(() => tenancyKeys.projectMembers(projectId.value)),
  queryFn: () => projectsApi.listMembers(projectId.value).then(r => r.data),
})

const myMembership = computed<ProjectMember | null>(() => {
  if (!members.value || !session.me) return null
  return members.value.find(m => m.user_id === session.me!.id) ?? null
})

const isOwner = computed(() => myMembership.value?.role === 'owner')

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

function roleLabel(member: ProjectMember): string {
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

function isMe(member: ProjectMember): boolean {
  return session.me?.id === member.user_id
}

function isInherited(member: ProjectMember): boolean {
  return member.is_inherited === true
}

function getRowActions(member: ProjectMember): Array<{ key: string; label: string; danger?: boolean }> {
  if (isMe(member) || isInherited(member) || !isOwner.value) return []
  const items: Array<{ key: string; label: string; danger?: boolean }> = []

  if (member.role === 'member') {
    items.push({ key: 'promote', label: t('tenancy.role.owner') })
  }
  if (member.role === 'owner') {
    items.push({ key: 'demote', label: t('tenancy.role.member') })
  }
  items.push({ key: 'remove', label: t('tenancy.member.removeConfirm'), danger: true })
  return items
}

async function onInvite(): Promise<void> {
  const email = inviteEmail.value.trim()
  if (!email) return
  invitePending.value = true
  inviteError.value = null
  try {
    await projectsApi.invite(projectId.value, email, inviteRole.value)
    inviteEmail.value = ''
    inviteRole.value = 'member'
    qc.invalidateQueries({ queryKey: tenancyKeys.projectMembers(projectId.value) })
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

async function onAction(key: string, member: ProjectMember): Promise<void> {
  if (key === 'promote') {
    const ok = await confirm({
      title: t('tenancy.member.changeRoleTitle'),
      message: t('tenancy.member.changeRoleBody', { email: member.email, role: t('tenancy.role.owner') }),
      variant: 'info',
    })
    if (!ok) return
    try {
      await projectsApi.setRole(projectId.value, member.user_id, 'owner')
      qc.invalidateQueries({ queryKey: tenancyKeys.projectMembers(projectId.value) })
      toast.success(t('tenancy.member.roleChanged'))
    } catch {
      toast.error(t('tenancy.project.loadError'))
    }
  } else if (key === 'demote') {
    const ok = await confirm({
      title: t('tenancy.member.changeRoleTitle'),
      message: t('tenancy.member.changeRoleBody', { email: member.email, role: t('tenancy.role.member') }),
      variant: 'info',
    })
    if (!ok) return
    try {
      await projectsApi.setRole(projectId.value, member.user_id, 'member')
      qc.invalidateQueries({ queryKey: tenancyKeys.projectMembers(projectId.value) })
      toast.success(t('tenancy.member.roleChanged'))
    } catch {
      toast.error(t('tenancy.project.loadError'))
    }
  } else if (key === 'remove') {
    const ok = await confirm({
      title: t('tenancy.member.removeTitle'),
      message: t('tenancy.member.removeBodyProject'),
      variant: 'error',
      confirmLabel: t('tenancy.member.removeConfirm'),
    })
    if (!ok) return
    try {
      await projectsApi.removeMember(projectId.value, member.user_id)
      qc.invalidateQueries({ queryKey: tenancyKeys.projectMembers(projectId.value) })
      toast.success(t('tenancy.member.removed'))
    } catch {
      toast.error(t('tenancy.project.loadError'))
    }
  }
}

const breadcrumbs = computed(() => [
  { label: t('tenancy.breadcrumb.home'), to: { name: 'tenancy.orgList' } },
  { label: t('tenancy.breadcrumb.projects'), to: { name: 'tenancy.projectList' } },
  { label: project.value?.name ?? '...', to: { name: 'tenancy.projectDetail', params: { id: projectId.value } } },
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
      v-if="isOwner"
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
          v-if="isMe(row as ProjectMember)"
          class="you-label"
        >
          {{ t('tenancy.member.you') }}
        </span>
      </template>

      <template #cell-role="{ row }">
        <span class="role-badges">
          <SBadge variant="neutral">
            {{ roleLabel(row as ProjectMember) }}
          </SBadge>
          <STooltip
            v-if="isInherited(row as ProjectMember)"
            :content="t('tenancy.member.inheritedTooltip')"
          >
            <SBadge variant="neutral">
              {{ t('tenancy.member.inherited') }}
            </SBadge>
          </STooltip>
        </span>
      </template>

      <template #cell-joined_at="{ row }">
        {{ formatRelative(row.joined_at) }}
      </template>

      <template #cell-actions="{ row }">
        <SDropdown
          v-if="getRowActions(row as ProjectMember).length > 0"
          :items="getRowActions(row as ProjectMember)"
          @select="(key: string) => onAction(key, row as ProjectMember)"
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

.role-badges {
  display: inline-flex;
  gap: 4px;
  align-items: center;
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
