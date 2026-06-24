import { useI18n } from 'vue-i18n'
import { useConfirmDialog, useToast } from '@shared/composables'
import { isProblemWithType } from '@shared/transport'
import type { QueryClient } from '@tanstack/vue-query'

interface MemberLike {
  user_id: string
  email: string
  role: 'owner' | 'member'
  is_original_creator?: boolean
  is_inherited?: boolean
}

type ActionItem = { key: string; label: string; danger?: boolean }

interface MemberActionsApi {
  setRole: (entityId: string, userId: string, role: 'owner' | 'member') => Promise<unknown>
  removeMember: (entityId: string, userId: string) => Promise<unknown>
}

interface UseMemberActionsOptions {
  api: MemberActionsApi
  queryKey: () => readonly unknown[]
  qc: QueryClient
  currentUserId: () => string | undefined
  canPromote: () => boolean
  canDemote: () => boolean
  canRemove: () => boolean
  removeMessage: () => string
  errorMessage: () => string
}

export function useMemberActions(options: UseMemberActionsOptions) {
  const { t } = useI18n()
  const { confirm } = useConfirmDialog()
  const toast = useToast()

  function getRowActions(member: MemberLike): ActionItem[] {
    if (member.is_original_creator) return []
    if (member.is_inherited) return []
    if (member.user_id === options.currentUserId()) return []

    const items: ActionItem[] = []
    if (member.role === 'member' && options.canPromote()) {
      items.push({ key: 'promote', label: t('tenancy.role.owner') })
    }
    if (member.role === 'owner' && options.canDemote()) {
      items.push({ key: 'demote', label: t('tenancy.role.member') })
    }
    if (options.canRemove()) {
      items.push({ key: 'remove', label: t('tenancy.member.removeConfirm'), danger: true })
    }
    return items
  }

  async function onAction(entityId: string, key: string, member: MemberLike): Promise<void> {
    if (key === 'promote' || key === 'demote') {
      const newRole = key === 'promote' ? 'owner' : 'member'
      const roleText = key === 'promote' ? t('tenancy.role.owner') : t('tenancy.role.member')
      const ok = await confirm({
        title: t('tenancy.member.changeRoleTitle'),
        message: t('tenancy.member.changeRoleBody', { email: member.email, role: roleText }),
        variant: 'info',
      })
      if (!ok) return
      try {
        await options.api.setRole(entityId, member.user_id, newRole as 'owner' | 'member')
        options.qc.invalidateQueries({ queryKey: options.queryKey() })
        toast.success(t('tenancy.member.roleChanged'))
      } catch {
        toast.error(options.errorMessage())
      }
    } else if (key === 'remove') {
      const ok = await confirm({
        title: t('tenancy.member.removeTitle'),
        message: options.removeMessage(),
        variant: 'error',
        confirmLabel: t('tenancy.member.removeConfirm'),
      })
      if (!ok) return
      try {
        await options.api.removeMember(entityId, member.user_id)
        options.qc.invalidateQueries({ queryKey: options.queryKey() })
        toast.success(t('tenancy.member.removed'))
      } catch (e: unknown) {
        if (isProblemWithType(e, '/tenancy/original-creator-conflict')) {
          toast.error(t('tenancy.member.cannotRemoveOC'))
        } else {
          toast.error(options.errorMessage())
        }
      }
    }
  }

  return { getRowActions, onAction }
}
