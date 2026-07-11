// Resolve the caller's authorization for a project (admin OR project owner).
// The synchronous route guard only knows the global admin flag, so per-project
// owner-gating is resolved here: admins skip the fetch; everyone else resolves
// their membership role. Phase 4α lifted this into `tenancy` (from `workflow`)
// keyed by `projectId` so the Concept Map owner panels across the agents,
// agent-groups, and conversation slices reuse it without a deep cross-slice
// import (the workflow variant is workspace-keyed and stays there).

import { computed, toValue, type MaybeRefOrGetter } from 'vue'
import { useQuery } from '@tanstack/vue-query'

import { useSessionStore } from '@slices/identity'

import { projectsApi } from '../api/projects'
import { tenancyKeys } from '../queries'

export function useProjectRole(projectId: MaybeRefOrGetter<string | undefined>) {
  const session = useSessionStore()
  const isAdmin = computed(() => session.me?.is_admin ?? false)
  const pid = computed(() => toValue(projectId) ?? '')

  const membersQuery = useQuery({
    queryKey: computed(() => tenancyKeys.projectMembers(pid.value)),
    queryFn: () => projectsApi.listMembers(pid.value),
    enabled: computed(() => !isAdmin.value && !!pid.value),
  })

  const isOwner = computed(() => {
    const me = session.me
    const members = membersQuery.data.value
    if (!me || !members) return false
    return members.find((m) => m.user_id === me.id)?.role === 'owner'
  })

  const isAuthorized = computed(() => isAdmin.value || isOwner.value)

  // True once authorization can be concluded, so callers don't hide an owner's
  // control mid-load (R11.10 / spec §8: a flash-hidden-then-shown control is a
  // correctness bug). Undecided while the project id is still resolving.
  const decided = computed(() => {
    if (isAdmin.value) return true
    if (!pid.value) return false
    return membersQuery.isSuccess.value || membersQuery.isError.value
  })

  return { isAdmin, isOwner, isAuthorized, decided }
}
