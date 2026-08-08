// First-party plugin registration entry point. Importing this module wires
// whatever bundled plugins exist — `ActivityHost.vue` imports it, so the
// registrations run before any lookup.

import { mandala9GridPlugin } from './mandala9grid'
import { registerActivityPlugin } from './registry'

/** Register the bundled set. Idempotent (the registry keys on `manifest.key`),
 *  and exported so a test that calls `clearActivityPlugins()` can restore it —
 *  the module-scope call below runs only once per module-cache lifetime. Mirrors
 *  the backend's `register_first_party_validators()`. */
export function registerBundledPlugins(): void {
  registerActivityPlugin(mandala9GridPlugin)
}

registerBundledPlugins()

export { getActivityPlugin, registerActivityPlugin, clearActivityPlugins } from './registry'
export { MANDALA_9GRID_KEY, mandala9GridPlugin } from './mandala9grid'
