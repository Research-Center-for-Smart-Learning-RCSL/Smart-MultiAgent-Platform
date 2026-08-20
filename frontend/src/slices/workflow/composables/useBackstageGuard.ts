// Every workflow read endpoint is Admin-or-project-owner ([R14.10], dossier
// 2026-08-20-orchestration-room-scoped-reads AC-6) — the trace is a backstage
// surface, and the server enforces that on list/get/validate/list_runs/
// list_steps rather than trusting the panel to stay hidden.
//
// The router guard only knows the global admin flag, so the per-project half is
// resolved here: an unauthorized visitor is sent away instead of landing on a
// page whose every request answers 403. `decided` exists so a legitimate owner
// is never redirected mid-load, and callers must gate their queries on
// `isAuthorized` too — the redirect is navigation, not cancellation, and a query
// that fires anyway still 403s on the way out.

import { watch } from 'vue'
import { useRouter } from 'vue-router'

import { useProjectRole } from './useProjectRole'

export function useBackstageGuard(workspaceId: string) {
  const router = useRouter()
  const { isAuthorized, decided } = useProjectRole(workspaceId)

  watch(
    [decided, isAuthorized],
    ([isDecided, ok]) => {
      if (isDecided && !ok) router.replace({ name: 'root' })
    },
    { immediate: true },
  )

  return { isAuthorized, decided }
}
