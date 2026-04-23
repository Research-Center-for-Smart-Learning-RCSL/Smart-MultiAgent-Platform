import { useMutation, useQueryClient } from '@tanstack/vue-query'
import { adminApi } from '../api/admin'
import { adminKeys } from '../queries'

export function useAdminActions() {
  const qc = useQueryClient()

  const banUser = useMutation({
    mutationFn: ({ userId, reason }: { userId: string; reason: string }) =>
      adminApi.banUser(userId, reason),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin', 'users'] }),
  })

  const unbanUser = useMutation({
    mutationFn: (userId: string) => adminApi.unbanUser(userId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin', 'users'] }),
  })

  const softDeleteUser = useMutation({
    mutationFn: (userId: string) => adminApi.softDeleteUser(userId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin', 'users'] }),
  })

  const hardDeleteUser = useMutation({
    mutationFn: (userId: string) => adminApi.hardDeleteUser(userId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin', 'users'] }),
  })

  const promoteAdmin = useMutation({
    mutationFn: (userId: string) => adminApi.promoteAdmin(userId),
    onSuccess: () => qc.invalidateQueries({ queryKey: adminKeys.admins() }),
  })

  const demoteAdmin = useMutation({
    mutationFn: (userId: string) => adminApi.demoteAdmin(userId),
    onSuccess: () => qc.invalidateQueries({ queryKey: adminKeys.admins() }),
  })

  const forceDeleteOrg = useMutation({
    mutationFn: (orgId: string) => adminApi.forceDeleteOrg(orgId),
    onSuccess: () => qc.invalidateQueries({ queryKey: adminKeys.orgs() }),
  })

  const createIpBan = useMutation({
    mutationFn: ({ cidr, reason }: { cidr: string; reason: string }) =>
      adminApi.createIpBan(cidr, reason),
    onSuccess: () => qc.invalidateQueries({ queryKey: adminKeys.ipBans() }),
  })

  const deleteIpBan = useMutation({
    mutationFn: (id: string) => adminApi.deleteIpBan(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: adminKeys.ipBans() }),
  })

  const patchRateLimit = useMutation({
    mutationFn: ({ key, patch }: { key: string; patch: { window_sec?: number; max_count?: number; scope?: string } }) =>
      adminApi.patchRateLimit(key, patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: adminKeys.rateLimits() }),
  })

  const restoreResource = useMutation({
    mutationFn: ({ type, id }: { type: string; id: string }) =>
      adminApi.restoreResource(type, id),
  })

  const resetGraphrag = useMutation({
    mutationFn: (configId: string) => adminApi.resetGraphrag(configId),
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
