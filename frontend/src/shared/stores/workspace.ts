import { ref, computed } from 'vue'
import { defineStore } from 'pinia'

const LS_KEY = 'smap-workspace'

interface PersistedState {
  orgId: string | null
  orgName: string | null
  projectId: string | null
  projectName: string | null
}

function loadFromStorage(): PersistedState {
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (raw) return JSON.parse(raw) as PersistedState
  } catch { /* ignore */ }
  return { orgId: null, orgName: null, projectId: null, projectName: null }
}

function saveToStorage(state: PersistedState): void {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(state))
  } catch { /* QuotaExceededError or SecurityError in private browsing */ }
}

export const useWorkspaceStore = defineStore('workspace', () => {
  const persisted = loadFromStorage()

  const orgId = ref<string | null>(persisted.orgId)
  const orgName = ref<string | null>(persisted.orgName)
  const projectId = ref<string | null>(persisted.projectId)
  const projectName = ref<string | null>(persisted.projectName)

  const hasOrg = computed(() => orgId.value !== null)
  const hasProject = computed(() => projectId.value !== null)

  function selectOrg(id: string, name: string): void {
    orgId.value = id
    orgName.value = name
    projectId.value = null
    projectName.value = null
    persist()
  }

  function selectProject(id: string, name: string): void {
    projectId.value = id
    projectName.value = name
    persist()
  }

  function clear(): void {
    orgId.value = null
    orgName.value = null
    projectId.value = null
    projectName.value = null
    persist()
  }

  function persist(): void {
    saveToStorage({
      orgId: orgId.value,
      orgName: orgName.value,
      projectId: projectId.value,
      projectName: projectName.value,
    })
  }

  return {
    orgId,
    orgName,
    projectId,
    projectName,
    hasOrg,
    hasProject,
    selectOrg,
    selectProject,
    clear,
  }
})
