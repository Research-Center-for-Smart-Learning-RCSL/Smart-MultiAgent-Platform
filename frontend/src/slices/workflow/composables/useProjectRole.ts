// Resolve the caller's role for a workspace's project. The synchronous route
// guard only knows the global admin flag, so per-project authorization (admin
// OR project owner) is resolved here: workspace -> project -> my membership.
// Admins skip the fetches entirely. Used to gate the backstage view and its
// entry links without duplicating the resolution.

import { computed } from 'vue'
import { useQuery } from '@tanstack/vue-query'

import { useSessionStore } from '@slices/identity'
import { getWorkspace } from '@slices/conversation'
import { projectsApi, tenancyKeys } from '@slices/tenancy'

export function useProjectRole(workspaceId: string) {
  const session = useSessionStore()
  const isAdmin = computed(() => session.me?.is_admin ?? false)

  const workspaceQuery = useQuery({
    queryKey: computed(() => ['workflow', 'project-role', 'workspace', workspaceId]),
    queryFn: () => getWorkspace(workspaceId),
    enabled: computed(() => !isAdmin.value && !!workspaceId),
  })

  const projectId = computed(() => workspaceQuery.data.value?.project_id ?? '')

  const membersQuery = useQuery({
    queryKey: computed(() => tenancyKeys.projectMembers(projectId.value)),
    queryFn: () => projectsApi.listMembers(projectId.value).then((r) => r.data),
    enabled: computed(() => !isAdmin.value && !!projectId.value),
  })

  const isOwner = computed(() => {
    const me = session.me
    const members = membersQuery.data.value
    if (!me || !members) return false
    return members.find((m) => m.user_id === me.id)?.role === 'owner'
  })

  const isAuthorized = computed(() => isAdmin.value || isOwner.value)

  // True once we can conclude authorization, so callers don't act mid-load
  // (e.g. redirect a legitimate owner before their membership has resolved).
  const decided = computed(() => {
    if (isAdmin.value) return true
    if (workspaceQuery.isError.value) return true
    return membersQuery.isSuccess.value || membersQuery.isError.value
  })

  return { isAdmin, isOwner, isAuthorized, decided }
}
