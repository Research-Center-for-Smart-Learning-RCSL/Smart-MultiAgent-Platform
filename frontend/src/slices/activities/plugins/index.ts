// First-party plugin registration entry point. Importing this module wires
// whatever bundled plugins exist — `ActivityHost.vue` imports it, so the
// registrations below run before any lookup.

import { mandala9GridPlugin } from './mandala9grid'
import { registerActivityPlugin } from './registry'

registerActivityPlugin(mandala9GridPlugin)

export { getActivityPlugin, registerActivityPlugin, clearActivityPlugins } from './registry'
export { MANDALA_9GRID_KEY, mandala9GridPlugin } from './mandala9grid'
