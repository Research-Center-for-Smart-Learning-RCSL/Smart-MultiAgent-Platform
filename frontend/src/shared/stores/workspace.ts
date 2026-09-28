import { ref, computed } from 'vue'
import { defineStore } from 'pinia'

const LS_KEY = 'smap-workspace'

interface PersistedState {
  orgId: string | null
  orgName: string | null
  projectId: string | null
  projectName: string | null
  workspaceId: string | null
  workspaceName: string | null
}

function loadFromStorage(): PersistedState {
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (raw) {
      const parsed = JSON.parse(raw)
      return {
        orgId: parsed.orgId ?? null,
        orgName: parsed.orgName ?? null,
        projectId: parsed.projectId ?? null,
        projectName: parsed.projectName ?? null,
        workspaceId: parsed.workspaceId ?? null,
        workspaceName: parsed.workspaceName ?? null,
      }
    }
  } catch { /* ignore */ }
  return { orgId: null, orgName: null, projectId: null, projectName: null, workspaceId: null, workspaceName: null }
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
  const workspaceId = ref<string | null>(persisted.workspaceId)
  const workspaceName = ref<string | null>(persisted.workspaceName)

  const hasOrg = computed(() => orgId.value !== null)
  const hasProject = computed(() => projectId.value !== null)
  const hasWorkspace = computed(() => workspaceId.value !== null)

  function selectOrg(id: string, name: string): void {
    orgId.value = id
    orgName.value = name
    projectId.value = null
    projectName.value = null
    workspaceId.value = null
    workspaceName.value = null
    persist()
  }

  function selectProject(id: string, name: string): void {
    projectId.value = id
    projectName.value = name
    workspaceId.value = null
    workspaceName.value = null
    persist()
  }

  function selectWorkspace(id: string, name: string): void {
    workspaceId.value = id
    workspaceName.value = name
    persist()
  }

  function clear(): void {
    orgId.value = null
    orgName.value = null
    projectId.value = null
    projectName.value = null
    workspaceId.value = null
    workspaceName.value = null
    persist()
  }

  function persist(): void {
    saveToStorage({
      orgId: orgId.value,
      orgName: orgName.value,
      projectId: projectId.value,
      projectName: projectName.value,
      workspaceId: workspaceId.value,
      workspaceName: workspaceName.value,
    })
  }

  return {
    orgId,
    orgName,
    projectId,
    projectName,
    workspaceId,
    workspaceName,
    hasOrg,
    hasProject,
    hasWorkspace,
    selectOrg,
    selectProject,
    selectWorkspace,
    clear,
  }
})
