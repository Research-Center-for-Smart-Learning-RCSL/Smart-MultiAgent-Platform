import { isGuestRestorePending, onPendingGuestRestore } from '@shared/transport'
import { restoreGuestSession } from '@slices/conversation'
import { useSessionStore } from '@slices/identity'

/**
 * Restore whichever session this tab can resume, before the router installs:
 * the account first, then (only if that failed) the guest session for the room
 * in the URL. See docs/tasks/2026-10-05-guest-frontend-session-lifecycle Q-1.
 */
export async function restoreSessionAtBoot(pathname: string): Promise<void> {
  const session = useSessionStore()
  onPendingGuestRestore(() => preferAccountOverPendingGuest())
  await session.hydrate()
  if (!session.isAuthenticated) await restoreGuestSession(pathname)
}

/**
 * A boot without a network cannot tell an account holder from a guest, so a
 * room with a hint is restored as a guest. Before that restore is retried the
 * account is tried again; if it answers, the page reloads into the account
 * rather than patching a room view that was built for a guest.
 */
export async function preferAccountOverPendingGuest(
  reload: () => void = () => window.location.reload(),
): Promise<boolean> {
  if (!isGuestRestorePending()) return false
  const session = useSessionStore()
  await session.hydrate()
  if (!session.isAuthenticated) return false
  reload()
  return true
}

/** The tab-focus re-hydrate, routed through the account-first check while a guest restore is pending. */
export async function rehydrateOnFocus(): Promise<void> {
  if (isGuestRestorePending()) await preferAccountOverPendingGuest()
  else await useSessionStore().hydrate()
}
