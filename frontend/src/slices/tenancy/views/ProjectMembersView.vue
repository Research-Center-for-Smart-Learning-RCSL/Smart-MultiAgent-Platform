<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  SPageHeader, SCard, STable, SAvatar, SBadge, SButton,
  SFormField, SInput, SSelect, SDropdown, SAlert, SEmptyState,
  STooltip,
} from '@shared/ui'
import { useToast } from '@shared/composables'
import { useSessionStore } from '@shared/stores/session'
import { isProblemWithType } from '@shared/transport'
import { UserGroupIcon, EllipsisVerticalIcon } from '@heroicons/vue/24/outline'
import { projectsApi, type ProjectMember } from '../api/projects'
import { tenancyKeys } from '../queries'
import { formatRelative } from '../utils/formatters'
import { roleLabel } from '../utils/roles'
import { useMemberActions } from '../composables/useMemberActions'
import { useProjectRole } from '../composables/useProjectRole'
import InviteAcceptLinkCard from '../components/InviteAcceptLinkCard.vue'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const toast = useToast()
const session = useSessionStore()
const qc = useQueryClient()

const projectId = computed(() => route.params.id as string)

const { data: project } = useQuery({
  queryKey: computed(() => tenancyKeys.project(projectId.value)),
  queryFn: () => projectsApi.get(projectId.value),
})

const { data: members, isLoading, isError, refetch } = useQuery({
  queryKey: computed(() => tenancyKeys.projectMembers(projectId.value)),
  queryFn: () => projectsApi.listMembers(projectId.value),
})

// Not the member list: project ownership is inherited, so an Org Owner manages
// this project while holding no `project_members` row at all. `is_moderator` is
// the server's own verdict — see useProjectRole's header.
const { isAuthorized: canManageMembers } = useProjectRole(projectId)

// The pool the picker reads (R6.10, Q-6). Empty both for a user-owned project
// and for a caller outside the parent Org, which the backend makes deliberately
// indistinguishable — so an empty pool only ever means "nobody to pick", never
// "this project has no org".
const {
  data: invitablePool,
  isError: isPoolError,
  isFetched: isPoolFetched,
} = useQuery({
  queryKey: computed(() => tenancyKeys.invitableMembers(projectId.value)),
  queryFn: () => projectsApi.listInvitableMembers(projectId.value),
  enabled: canManageMembers,
})

const inviteEmail = ref('')
const invitePickedUserId = ref('')
const inviteRole = ref<'owner' | 'member'>('member')
const invitePending = ref(false)
const inviteError = ref<string | null>(null)
const inviteLink = ref<{ email: string; acceptUrl: string } | null>(null)

// The picker only exists when there is something to pick. Its absence is not an
// error state: a user-owned project has no pool by construction.
const hasPool = computed(() => (invitablePool.value?.length ?? 0) > 0)

// The form waits for the pool, because `hasPool` is false while the query is in
// flight: rendering on `canManageMembers` alone shows the address field one
// round trip early, then swaps it for the picker under anyone who started
// typing. One extra request's wait beats a control changing shape mid-keystroke.
const invitePoolSettled = computed(() => isPoolFetched.value || isPoolError.value)
const byEmail = ref(false)
const usePicker = computed(() => hasPool.value && !byEmail.value)

function toggleInviteMode(): void {
  byEmail.value = !byEmail.value
  // The error belongs to the control that produced it; carrying it across would
  // mark the other control invalid for a value it has never seen.
  inviteError.value = null
}

const pickerOptions = computed(() =>
  (invitablePool.value ?? []).map(m => ({ value: m.user_id, label: m.email })),
)

const pickedEmail = computed(
  () => (invitablePool.value ?? []).find(m => m.user_id === invitePickedUserId.value)?.email ?? '',
)

const canSubmitInvite = computed(() =>
  usePicker.value ? !!pickedEmail.value : !!inviteEmail.value.trim(),
)

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

const { getRowActions, onAction } = useMemberActions({
  api: { setRole: projectsApi.setRole, removeMember: projectsApi.removeMember },
  queryKey: () => tenancyKeys.projectMembers(projectId.value),
  qc,
  currentUserId: () => session.me?.id,
  canPromote: () => canManageMembers.value,
  canDemote: () => canManageMembers.value,
  canRemove: () => canManageMembers.value,
  removeMessage: () => t('tenancy.member.removeBodyProject'),
  errorMessage: () => t('tenancy.project.loadError'),
})

function isMe(member: ProjectMember): boolean {
  return session.me?.id === member.user_id
}

function isInherited(member: ProjectMember): boolean {
  return member.is_inherited === true
}

// STable's generic constrains T to Record<string, unknown>; ProjectMember has
// no index signature by design, so intersect it in only for this cast (the
// runtime shape is unchanged — plain ProjectMember objects from the API).
type ProjectMemberRow = ProjectMember & Record<string, unknown>

const tableData = computed<ProjectMemberRow[]>(() => (members.value ?? []) as unknown as ProjectMemberRow[])

async function onInvite(): Promise<void> {
  const email = usePicker.value ? pickedEmail.value : inviteEmail.value.trim()
  if (!email) return
  invitePending.value = true
  inviteError.value = null
  // Cleared up front, not on success: a failed second invite must not leave the
  // first one's link on screen under an error toast.
  inviteLink.value = null
  try {
    const invite = await projectsApi.invite(projectId.value, email, inviteRole.value)
    inviteEmail.value = ''
    invitePickedUserId.value = ''
    inviteRole.value = 'member'
    // The invitee leaves the pool the moment the invite exists, so the picker
    // cannot offer them again and hit the duplicate path it exists to avoid.
    qc.invalidateQueries({ queryKey: tenancyKeys.projectMembers(projectId.value) })
    qc.invalidateQueries({ queryKey: tenancyKeys.invitableMembers(projectId.value) })
    inviteLink.value = invite.accept_url ? { email, acceptUrl: invite.accept_url } : null
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
    >
      <template #actions>
        <SButton
          variant="secondary"
          size="sm"
          @click="() => router.push({
            name: 'tenancy.projectMemberGroups',
            params: { id: projectId },
          })"
        >
          {{ t('tenancy.breadcrumb.memberGroups') }}
        </SButton>
      </template>
    </SPageHeader>

    <!-- Invite form -->
    <SCard
      v-if="canManageMembers && invitePoolSettled"
      variant="flat"
      class="invite-card"
    >
      <form
        class="invite-form"
        @submit.prevent="onInvite"
      >
        <SFormField
          v-if="usePicker"
          :label="t('tenancy.member.invitePickLabel')"
          name="invitePickedUser"
          :error="inviteError ?? ''"
          required
          class="invite-email"
        >
          <SSelect
            v-model="invitePickedUserId"
            :options="pickerOptions"
            :placeholder="t('tenancy.member.invitePickPlaceholder')"
            :error="!!inviteError"
            :disabled="invitePending"
          />
        </SFormField>

        <SFormField
          v-else
          :label="'Email'"
          name="inviteEmail"
          :error="inviteError ?? ''"
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
          :disabled="invitePending || !canSubmitInvite"
          class="invite-btn"
        >
          {{ t('tenancy.member.sendInvite') }}
        </SButton>
      </form>

      <!-- The escape hatch Q-6 requires: the pool only holds people already in
           the parent Org, and inviting someone who is not is the common case a
           picker alone would make impossible. -->
      <SButton
        v-if="hasPool"
        variant="ghost"
        size="sm"
        class="invite-mode-toggle"
        @click="toggleInviteMode"
      >
        {{ byEmail ? t('tenancy.member.inviteByPicker') : t('tenancy.member.inviteByEmail') }}
      </SButton>

      <p
        v-if="isPoolError"
        class="invite-pool-error"
        role="status"
      >
        {{ t('tenancy.member.invitePoolError') }}
      </p>
    </SCard>

    <InviteAcceptLinkCard
      v-if="inviteLink"
      :email="inviteLink.email"
      :accept-url="inviteLink.acceptUrl"
      class="invite-link-card"
      @dismiss="inviteLink = null"
    />

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
          {{ t('tenancy.common.retry') }}
        </SButton>
      </template>
    </SAlert>

    <!-- Member table -->
    <STable
      v-else
      :columns="columns"
      :data="tableData"
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
          v-if="isMe(row)"
          class="you-label"
        >
          {{ t('tenancy.member.you') }}
        </span>
      </template>

      <template #cell-role="{ row }">
        <span class="role-badges">
          <SBadge variant="neutral">
            {{ roleLabel(row, t) }}
          </SBadge>
          <STooltip
            v-if="isInherited(row)"
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
          v-if="getRowActions(row).length > 0"
          :items="getRowActions(row)"
          @select="(key: string) => onAction(projectId, key, row)"
        >
          <template #trigger>
            <SButton
              variant="ghost"
              size="sm"
              icon-only
              :aria-label="t('tenancy.member.actions')"
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
@import '../styles/member-form.css';

.role-badges {
  display: inline-flex;
  gap: 4px;
  align-items: center;
}

.invite-mode-toggle {
  margin-top: -0.5rem;
}

.invite-pool-error {
  margin: 0.5rem 0 0;
  font-size: 0.75rem;
  color: var(--color-danger);
}

.invite-link-card {
  margin-bottom: 1rem;
}
</style>
