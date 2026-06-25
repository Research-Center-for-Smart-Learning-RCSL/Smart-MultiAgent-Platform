import { computed, ref } from 'vue'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { errorMessage } from '@shared/errors'
import { keysKeys } from '../queries'
import {
  keyGroupsApi,
  type KeyGroup,
  type KeyGroupDetail,
  type MemberPatch,
} from '../api/key-groups'

export function useKeyGroups(projectId: () => string) {
  const qc = useQueryClient()

  const { data, error: queryError, refetch } = useQuery({
    queryKey: computed(() => keysKeys.keyGroups(projectId())),
    queryFn: () => keyGroupsApi.listForProject(projectId()).then((r) => r.data),
    enabled: computed(() => !!projectId()),
  })

  const groups = computed<KeyGroup[]>(() => data.value ?? [])
  const error = computed(() => queryError.value ? errorMessage(queryError.value) : null)

  async function reload(): Promise<void> {
    await refetch()
  }

  async function create(name: string): Promise<void> {
    const pid = projectId()
    if (!pid) return
    await keyGroupsApi.create(pid, name)
    await qc.invalidateQueries({ queryKey: keysKeys.keyGroups(pid) })
  }

  async function remove(groupId: string): Promise<void> {
    await keyGroupsApi.remove(groupId)
    await qc.invalidateQueries({ queryKey: keysKeys.keyGroups(projectId()) })
  }

  return { groups, error, reload, create, remove }
}

export function useKeyGroupDetail(groupId: () => string) {
  const qc = useQueryClient()

  const { data, error: queryError, refetch } = useQuery({
    queryKey: computed(() => keysKeys.keyGroup(groupId())),
    queryFn: () => keyGroupsApi.get(groupId()).then((r) => r.data),
    enabled: computed(() => !!groupId()),
  })

  const detail = computed<KeyGroupDetail | null>(() => data.value ?? null)
  const error = computed(() => queryError.value ? errorMessage(queryError.value) : null)
  const optimisticDetail = ref<KeyGroupDetail | null>(null)

  const effectiveDetail = computed(() => optimisticDetail.value ?? detail.value ?? null)

  async function reload(): Promise<void> {
    optimisticDetail.value = null
    await refetch()
  }

  async function addMember(keyId: string): Promise<void> {
    const id = groupId()
    if (!id) return
    await keyGroupsApi.addMember(id, keyId)
    await qc.invalidateQueries({ queryKey: keysKeys.keyGroup(id) })
  }

  async function removeMember(keyId: string): Promise<void> {
    const id = groupId()
    if (!id) return
    await keyGroupsApi.removeMember(id, keyId)
    await qc.invalidateQueries({ queryKey: keysKeys.keyGroup(id) })
  }

  async function patchMember(keyId: string, patch: MemberPatch): Promise<void> {
    const id = groupId()
    if (!id) return
    await keyGroupsApi.patchMember(id, keyId, patch)
    await qc.invalidateQueries({ queryKey: keysKeys.keyGroup(id) })
  }

  async function rename(name: string): Promise<void> {
    const id = groupId()
    if (!id) return
    await keyGroupsApi.rename(id, name)
    await qc.invalidateQueries({ queryKey: keysKeys.keyGroup(id) })
  }

  async function remove(): Promise<void> {
    const id = groupId()
    if (!id) return
    await keyGroupsApi.remove(id)
  }

  async function reorder(priorities: Record<string, number>): Promise<void> {
    const id = groupId()
    if (!id) return
    const snapshot = detail.value
    if (snapshot) {
      const patched = snapshot.members
        .map((m) => ({ ...m, priority: priorities[m.key_id] ?? m.priority }))
        .sort((a, b) => a.priority - b.priority)
      optimisticDetail.value = { ...snapshot, members: patched }
    }
    try {
      await keyGroupsApi.reorder(id, priorities)
      optimisticDetail.value = null
      await qc.invalidateQueries({ queryKey: keysKeys.keyGroup(id) })
    } catch (e) {
      optimisticDetail.value = null
      await qc.invalidateQueries({ queryKey: keysKeys.keyGroup(id) })
      throw e
    }
  }

  return {
    detail: effectiveDetail,
    error,
    reload,
    rename,
    remove,
    addMember,
    removeMember,
    patchMember,
    reorder,
  }
}
