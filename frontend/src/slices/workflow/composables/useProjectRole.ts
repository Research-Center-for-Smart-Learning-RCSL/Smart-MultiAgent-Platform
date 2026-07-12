// Resolve the caller's role for a workspace's project. The synchronous route
// guard only knows the global admin flag, so per-project authorization (admin
// OR project owner) is resolved here: workspace -> project -> my membership.
// Used to gate the backstage view and its entry links.
//
// The membership -> role resolution (owner lookup, decided/authorized) is
// delegated to tenancy's project-keyed useProjectRole; this composable only
// adds the workspace -> project_id lookup in front and folds the workspace
// edge cases into `decided`.

import { computed } from 'vue'
import { useQuery } from '@tanstack/vue-query'

import { useSessionStore } from '@slices/identity'
import { getWorkspace } from '@slices/conversation'
import { useProjectRole as useProjectRoleForProject } from '@slices/tenancy'

export function useProjectRole(workspaceId: string) {
  const session = useSessionStore()
  const isAdmin = computed(() => session.me?.is_admin ?? false)

  const workspaceQuery = useQuery({
    queryKey: computed(() => ['workflow', 'project-role', 'workspace', workspaceId]),
    queryFn: () => getWorkspace(workspaceId),
    enabled: computed(() => !isAdmin.value && !!workspaceId),
  })

  const projectId = computed(() => workspaceQuery.data.value?.project_id ?? '')
  const role = useProjectRoleForProject(projectId)

  // True once we can conclude authorization, so callers don't act mid-load
  // (e.g. redirect a legitimate owner before their membership has resolved).
  const decided = computed(() => {
    if (isAdmin.value) return true
    if (workspaceQuery.isError.value) return true
    // Workspace resolved but carried no project_id: members can't be fetched,
    // so conclude (deny) rather than hang forever waiting on a disabled query.
    if (workspaceQuery.isSuccess.value && !projectId.value) return true
    return role.decided.value
  })

  return {
    isAdmin,
    isOwner: role.isOwner,
    isAuthorized: role.isAuthorized,
    decided,
  }
}
