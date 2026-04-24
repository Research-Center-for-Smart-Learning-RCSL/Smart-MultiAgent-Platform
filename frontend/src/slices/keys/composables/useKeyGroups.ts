import { ref } from 'vue'
import {
  keyGroupsApi,
  type KeyGroup,
  type KeyGroupDetail,
  type MemberPatch,
} from '../api/key-groups'

export function useKeyGroups(projectId: () => string) {
  const groups = ref<KeyGroup[]>([])
  const error = ref<string | null>(null)

  async function reload(): Promise<void> {
    const pid = projectId()
    if (!pid) {
      groups.value = []
      return
    }
    try {
      const { data } = await keyGroupsApi.listForProject(pid)
      groups.value = data
    } catch (e) {
      error.value = detail(e)
    }
  }

  async function create(name: string): Promise<void> {
    const pid = projectId()
    if (!pid) return
    try {
      await keyGroupsApi.create(pid, name)
    } catch (e) {
      error.value = detail(e)
    }
    await reload()
  }

  async function remove(groupId: string): Promise<void> {
    try {
      await keyGroupsApi.remove(groupId)
    } catch (e) {
      error.value = detail(e)
    }
    await reload()
  }

  return { groups, error, reload, create, remove }
}

export function useKeyGroupDetail(groupId: () => string) {
  const detailData = ref<KeyGroupDetail | null>(null)
  const error = ref<string | null>(null)

  async function reload(): Promise<void> {
    const id = groupId()
    if (!id) {
      detailData.value = null
      return
    }
    try {
      const { data } = await keyGroupsApi.get(id)
      detailData.value = data
    } catch (e) {
      error.value = detail(e)
    }
  }

  async function addMember(keyId: string): Promise<void> {
    const id = groupId()
    if (!id) return
    try {
      await keyGroupsApi.addMember(id, keyId)
    } catch (e) {
      error.value = detail(e)
    }
    await reload()
  }

  async function removeMember(keyId: string): Promise<void> {
    const id = groupId()
    if (!id) return
    try {
      await keyGroupsApi.removeMember(id, keyId)
    } catch (e) {
      error.value = detail(e)
    }
    await reload()
  }

  async function patchMember(keyId: string, patch: MemberPatch): Promise<void> {
    const id = groupId()
    if (!id) return
    try {
      await keyGroupsApi.patchMember(id, keyId, patch)
    } catch (e) {
      error.value = detail(e)
    }
    await reload()
  }

  async function reorder(priorities: Record<string, number>): Promise<void> {
    const id = groupId()
    if (!id) return
    // Optimistic: apply locally, then confirm with server.
    const snapshot = detailData.value
    if (snapshot) {
      const patched = snapshot.members
        .map((m) => ({ ...m, priority: priorities[m.key_id] ?? m.priority }))
        .sort((a, b) => a.priority - b.priority)
      detailData.value = { ...snapshot, members: patched }
    }
    try {
      await keyGroupsApi.reorder(id, priorities)
    } catch (e) {
      error.value = detail(e)
      detailData.value = snapshot // immediate visual rollback
      await reload()              // then sync authoritative order from server
    }
  }

  return {
    detail: detailData,
    error,
    reload,
    addMember,
    removeMember,
    patchMember,
    reorder,
  }
}

function detail(e: unknown): string {
  const any_ = e as { response?: { data?: { detail?: string; title?: string } } }
  return any_.response?.data?.detail ?? any_.response?.data?.title ?? 'request failed'
}
