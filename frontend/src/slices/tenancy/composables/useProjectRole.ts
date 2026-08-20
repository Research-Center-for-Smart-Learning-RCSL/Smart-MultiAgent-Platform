// Resolve the caller's authorization for a project (admin OR project owner).
// The synchronous route guard only knows the global admin flag, so per-project
// owner-gating is resolved here: admins skip the fetch; everyone else reads the
// server's own verdict. Phase 4α lifted this into `tenancy` (from `workflow`)
// keyed by `projectId` so the Concept Map owner panels across the agents,
// agent-groups, and conversation slices reuse it without a deep cross-slice
// import (the workflow variant is workspace-keyed and stays there).
//
// It reads `ProjectOut.is_moderator` rather than scanning the member list for
// its own `owner` row, and that is the whole point: ownership is inherited
// (R5.03), so an Org Owner moderates every project of that org while holding no
// `project_members` row at all. The member-list reading concluded "not an
// owner" for exactly those people, while every server gate concluded the
// opposite — so the client hid controls, and redirected away from pages, that
// the caller was entitled to. The bit is computed server-side by the batch form
// of the gates' own predicate, so the two cannot disagree.

import { computed, toValue, type MaybeRefOrGetter } from 'vue'
import { useQuery } from '@tanstack/vue-query'

import { useSessionStore } from '@slices/identity'

import { projectsApi } from '../api/projects'
import { tenancyKeys } from '../queries'

export function useProjectRole(projectId: MaybeRefOrGetter<string | undefined>) {
  const session = useSessionStore()
  const isAdmin = computed(() => session.me?.is_admin ?? false)
  const pid = computed(() => toValue(projectId) ?? '')

  const projectQuery = useQuery({
    queryKey: computed(() => tenancyKeys.project(pid.value)),
    queryFn: () => projectsApi.get(pid.value),
    enabled: computed(() => !isAdmin.value && !!pid.value),
  })

  const isOwner = computed(() => projectQuery.data.value?.is_moderator ?? false)

  const isAuthorized = computed(() => isAdmin.value || isOwner.value)

  // True once authorization can be concluded, so callers don't hide an owner's
  // control mid-load (R11.10 / spec §8: a flash-hidden-then-shown control is a
  // correctness bug). Undecided while the project id is still resolving.
  const decided = computed(() => {
    if (isAdmin.value) return true
    if (!pid.value) return false
    return projectQuery.isSuccess.value || projectQuery.isError.value
  })

  return { isAdmin, isOwner, isAuthorized, decided }
}
