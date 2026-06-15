import { useMutation, useQueryClient } from '@tanstack/vue-query'
import { useI18n } from 'vue-i18n'
import { useToast } from '@shared/composables'
import { adminApi } from '../api/admin'
import { adminKeys } from '../queries'

export function useAdminActions() {
  const { t } = useI18n()
  const qc = useQueryClient()
  const toast = useToast()

  const banUser = useMutation({
    mutationFn: ({ userId, reason }: { userId: string; reason: string }) =>
      adminApi.banUser(userId, reason),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin', 'users'] }),
    onError: () => toast.error(t('admin.actionErrors.banFailed')),
  })

  const unbanUser = useMutation({
    mutationFn: (userId: string) => adminApi.unbanUser(userId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin', 'users'] }),
    onError: () => toast.error(t('admin.actionErrors.unbanFailed')),
  })

  const softDeleteUser = useMutation({
    mutationFn: (userId: string) => adminApi.softDeleteUser(userId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin', 'users'] }),
    onError: () => toast.error(t('admin.actionErrors.deleteFailed')),
  })

  const hardDeleteUser = useMutation({
    mutationFn: (userId: string) => adminApi.hardDeleteUser(userId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin', 'users'] }),
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
    onError: () => toast.error(t('admin.actionErrors.demoteFailed')),
  })

  const forceDeleteOrg = useMutation({
    mutationFn: (orgId: string) => adminApi.forceDeleteOrg(orgId),
    onSuccess: () => qc.invalidateQueries({ queryKey: adminKeys.orgs() }),
    onError: () => toast.error(t('admin.actionErrors.deleteOrgFailed')),
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
    mutationFn: ({ type, id }: { type: string; id: string }) =>
      adminApi.restoreResource(type, id),
    onSuccess: () => qc.invalidateQueries({ queryKey: adminKeys.orgs() }),
    onError: () => toast.error(t('admin.actionErrors.restoreFailed')),
  })

  const resetGraphrag = useMutation({
    mutationFn: (configId: string) => adminApi.resetGraphrag(configId),
    onError: () => toast.error(t('admin.actionErrors.resetGraphragFailed')),
  })

  return {
    banUser,
    unbanUser,
    softDeleteUser,
    hardDeleteUser,
    promoteAdmin,
    demoteAdmin,
    forceDeleteOrg,
    createIpBan,
    deleteIpBan,
    patchRateLimit,
    restoreResource,
    resetGraphrag,
  }
}
