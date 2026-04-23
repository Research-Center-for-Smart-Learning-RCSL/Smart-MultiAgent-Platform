import { useMutation, useQueryClient } from '@tanstack/vue-query'
import { ElMessage } from 'element-plus'
import { adminApi } from '../api/admin'
import { adminKeys } from '../queries'

export function useAdminActions() {
  const qc = useQueryClient()

  const banUser = useMutation({
    mutationFn: ({ userId, reason }: { userId: string; reason: string }) =>
      adminApi.banUser(userId, reason),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin', 'users'] }),
    onError: () => ElMessage.error('Failed to ban user.'),
  })

  const unbanUser = useMutation({
    mutationFn: (userId: string) => adminApi.unbanUser(userId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin', 'users'] }),
    onError: () => ElMessage.error('Failed to unban user.'),
  })

  const softDeleteUser = useMutation({
    mutationFn: (userId: string) => adminApi.softDeleteUser(userId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin', 'users'] }),
    onError: () => ElMessage.error('Failed to delete user.'),
  })

  const hardDeleteUser = useMutation({
    mutationFn: (userId: string) => adminApi.hardDeleteUser(userId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin', 'users'] }),
    onError: () => ElMessage.error('Failed to permanently delete user.'),
  })

  const promoteAdmin = useMutation({
    mutationFn: (userId: string) => adminApi.promoteAdmin(userId),
    onSuccess: () => qc.invalidateQueries({ queryKey: adminKeys.admins() }),
    onError: () => ElMessage.error('Failed to promote user to admin.'),
  })

  const demoteAdmin = useMutation({
    mutationFn: (userId: string) => adminApi.demoteAdmin(userId),
    onSuccess: () => qc.invalidateQueries({ queryKey: adminKeys.admins() }),
    onError: () => ElMessage.error('Failed to demote admin.'),
  })

  const forceDeleteOrg = useMutation({
    mutationFn: (orgId: string) => adminApi.forceDeleteOrg(orgId),
    onSuccess: () => qc.invalidateQueries({ queryKey: adminKeys.orgs() }),
    onError: () => ElMessage.error('Failed to delete organisation.'),
  })

  const createIpBan = useMutation({
    mutationFn: ({ cidr, reason }: { cidr: string; reason: string }) =>
      adminApi.createIpBan(cidr, reason),
    onSuccess: () => qc.invalidateQueries({ queryKey: adminKeys.ipBans() }),
    onError: () => ElMessage.error('Failed to create IP ban.'),
  })

  const deleteIpBan = useMutation({
    mutationFn: (id: string) => adminApi.deleteIpBan(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: adminKeys.ipBans() }),
    onError: () => ElMessage.error('Failed to remove IP ban.'),
  })

  const patchRateLimit = useMutation({
    mutationFn: ({ key, patch }: { key: string; patch: { window_sec?: number; max_count?: number; scope?: string } }) =>
      adminApi.patchRateLimit(key, patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: adminKeys.rateLimits() }),
    onError: () => ElMessage.error('Failed to update rate limit.'),
  })

  const restoreResource = useMutation({
    mutationFn: ({ type, id }: { type: string; id: string }) =>
      adminApi.restoreResource(type, id),
    onSuccess: () => qc.invalidateQueries({ queryKey: adminKeys.orgs() }),
    onError: () => ElMessage.error('Failed to restore resource.'),
  })

  const resetGraphrag = useMutation({
    mutationFn: (configId: string) => adminApi.resetGraphrag(configId),
    onError: () => ElMessage.error('Failed to reset GraphRAG index.'),
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
