// Shared cleanup registry so the session store can clear all slice state
// without importing each slice directly (breaking the dependency cycle H14).
//
// Each slice registers its cleanup callback at module-init time. On logout
// the session store calls `runAllCleanups()` which invokes every registered
// callback in registration order.

const cleanupCallbacks: Array<() => void> = []

/** Register a cleanup callback. Idempotent — the same function reference
 *  is only stored once. */
export function registerCleanup(fn: () => void): void {
  if (!cleanupCallbacks.includes(fn)) {
    cleanupCallbacks.push(fn)
  }
}

/** Run every registered cleanup callback (called by session.clear()). */
export function runAllCleanups(): void {
  for (const fn of cleanupCallbacks) {
    fn()
  }
}
