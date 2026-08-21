import { useMutation, useQueryClient } from '@tanstack/vue-query'
import { useI18n } from 'vue-i18n'
import { useConfirmDialog, useToast } from '@shared/composables'
import { ApiError } from '@shared/errors'
import { isProblemWithType } from '@shared/transport'
import { adminApi, type RestoreResourceType } from '../api/admin'
import { adminKeys } from '../queries'

export function useAdminActions() {
  const { t } = useI18n()
  const qc = useQueryClient()
  const toast = useToast()
  const { prompt } = useConfirmDialog()

  async function promptBan(userId: string): Promise<void> {
    const reason = await prompt({
      title: t('admin.users.banDialogTitle'),
      message: t('admin.users.banDialogMessage'),
      confirmLabel: t('admin.users.banDialogConfirm'),
      cancelLabel: t('app.cancel'),
      inputPattern: /\S+/,
      inputErrorMessage: t('admin.users.banDialogReasonRequired'),
      variant: 'warning',
    })
    if (reason) banUser.mutate({ userId, reason })
  }

  const _invalidateUserQueries = () => {
    qc.invalidateQueries({ queryKey: ['admin', 'users'] })
    qc.invalidateQueries({ queryKey: ['admin', 'user'] })
  }

  // R6.18 provisioning. Both mints have refusals an Admin needs told apart:
  // "that address already has an account" and "your own domain policy forbids
  // it" are different problems with different fixes, and the generic failure
  // toast would send an operator looking in the wrong place.
  const createUser = useMutation({
    mutationFn: ({ email, displayName }: { email: string; displayName: string | null }) =>
      adminApi.createUser(email, displayName),
    onSuccess: _invalidateUserQueries,
    onError: (err) => {
      if (isProblemWithType(err, 'auth/email-taken')) {
        toast.error(t('admin.users.createEmailTaken'))
      } else if (isProblemWithType(err, 'auth/domain-denied')) {
        toast.error(t('admin.users.createDomainDenied'))
      } else {
        toast.error(t('admin.actionErrors.createUserFailed'))
      }
    },
  })

  const reissueActivationLinks = useMutation({
    mutationFn: (userId: string) => adminApi.reissueActivationLinks(userId),
    onError: (err) => {
      if (isProblemWithType(err, 'admin/activation-links-rate-limited')) {
        toast.error(t('admin.users.linksRateLimited'))
      } else if (err instanceof ApiError && err.status === 409) {
        // A plain 409 from the route, not a typed Problem: the guard refuses an
        // account that can already authenticate (a password *or* a linked
        // identity), which is the one refusal an Admin must not read as a bug.
        toast.error(t('admin.users.alreadyActivated'))
      } else {
        toast.error(t('admin.actionErrors.reissueLinksFailed'))
      }
    },
  })

  const banUser = useMutation({
    mutationFn: ({ userId, reason }: { userId: string; reason: string }) =>
      adminApi.banUser(userId, reason),
    onSuccess: _invalidateUserQueries,
    onError: () => toast.error(t('admin.actionErrors.banFailed')),
  })

  const unbanUser = useMutation({
    mutationFn: (userId: string) => adminApi.unbanUser(userId),
    onSuccess: _invalidateUserQueries,
    onError: () => toast.error(t('admin.actionErrors.unbanFailed')),
  })

  const softDeleteUser = useMutation({
    mutationFn: (userId: string) => adminApi.softDeleteUser(userId),
    onSuccess: _invalidateUserQueries,
    onError: () => toast.error(t('admin.actionErrors.deleteFailed')),
  })

  const hardDeleteUser = useMutation({
    mutationFn: (userId: string) => adminApi.hardDeleteUser(userId),
    onSuccess: _invalidateUserQueries,
    onError: () => toast.error(t('admin.actionErrors.hardDeleteFailed')),
  })

  const promoteAdmin = useMutation({
    mutationFn: (userId: string) => adminApi.promoteAdmin(userId),
    onSuccess: () => qc.invalidateQueries({ queryKey: adminKeys.admins() }),
    onError: () => toast.error(t('admin.actionErrors.promoteFailed')),
  })

  const demoteAdmin = useMutation({
    mutationFn: (userId: string) => adminApi.demoteAdmin(userId),
    onSuccess: () => qc.invalidateQueries({ queryKey: adminKeys.admins() }),
    onError: (err) => toast.error(t(
      isProblemWithType(err, 'admin/last-admin')
        ? 'admin.users.lastAdminDemote'
        : 'admin.actionErrors.demoteFailed',
    )),
  })

  const forceDeleteOrg = useMutation({
    mutationFn: (orgId: string) => adminApi.forceDeleteOrg(orgId),
    onSuccess: () => qc.invalidateQueries({ queryKey: adminKeys.orgs() }),
    onError: () => toast.error(t('admin.actionErrors.deleteOrgFailed')),
  })

  const forceTransferOrg = useMutation({
    mutationFn: ({ orgId, targetUserId }: { orgId: string; targetUserId: string }) =>
      adminApi.forceTransferOC(orgId, targetUserId),
    onSuccess: () => qc.invalidateQueries({ queryKey: adminKeys.orgs() }),
    onError: () => toast.error(t('admin.orgs.transferFailed')),
  })

  const createIpBan = useMutation({
    mutationFn: ({ cidr, reason }: { cidr: string; reason: string }) =>
      adminApi.createIpBan(cidr, reason),
    onSuccess: () => qc.invalidateQueries({ queryKey: adminKeys.ipBans() }),
    onError: () => toast.error(t('admin.actionErrors.createIpBanFailed')),
  })

  const deleteIpBan = useMutation({
    mutationFn: (id: string) => adminApi.deleteIpBan(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: adminKeys.ipBans() }),
    onError: () => toast.error(t('admin.actionErrors.removeIpBanFailed')),
  })

  const patchRateLimit = useMutation({
    mutationFn: ({ key, patch }: { key: string; patch: { window_sec?: number; max_count?: number; scope?: string } }) =>
      adminApi.patchRateLimit(key, patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: adminKeys.rateLimits() }),
    onError: () => toast.error(t('admin.actionErrors.rateLimitFailed')),
  })

  const restoreResource = useMutation({
    mutationFn: ({ type, id }: { type: RestoreResourceType; id: string }) =>
      adminApi.restoreResource(type, id),
    onSuccess: (_data, { type }) => {
      if (type === 'user') {
        _invalidateUserQueries()
      } else {
        const keyMap: Record<string, readonly unknown[]> = {
          org: adminKeys.orgs(),
          project: adminKeys.projects(),
        }
        const key = keyMap[type]
        if (key) qc.invalidateQueries({ queryKey: key })
      }
    },
    onError: () => toast.error(t('admin.actionErrors.restoreFailed')),
  })

  const resetGraphrag = useMutation({
    mutationFn: (configId: string) => adminApi.resetGraphrag(configId),
    onError: () => toast.error(t('admin.actionErrors.resetGraphragFailed')),
  })

  return {
    promptBan,
    createUser,
    reissueActivationLinks,
    banUser,
    unbanUser,
    softDeleteUser,
    hardDeleteUser,
    promoteAdmin,
    demoteAdmin,
    forceDeleteOrg,
    forceTransferOrg,
    createIpBan,
    deleteIpBan,
    patchRateLimit,
    restoreResource,
    resetGraphrag,
  }
}
