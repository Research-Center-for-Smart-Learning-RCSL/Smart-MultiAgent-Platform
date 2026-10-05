import { restoreGuestSession } from '@slices/conversation'
import { useSessionStore } from '@slices/identity'

/**
 * Restore whichever session this tab can resume, before the router installs:
 * the account first, then (only if that failed) the guest session for the room
 * in the URL. See docs/tasks/2026-10-05-guest-frontend-session-lifecycle Q-1.
 */
export async function restoreSessionAtBoot(pathname: string): Promise<void> {
  const session = useSessionStore()
  await session.hydrate()
  if (!session.isAuthenticated) await restoreGuestSession(pathname)
}
