import type { ActivityPlugin } from './types'

/** Identity helper that gives a first-party plugin author full type-checking of
 *  the {@link ActivityPlugin} contract at the definition site. */
export function defineActivityPlugin(plugin: ActivityPlugin): ActivityPlugin {
  return plugin
}
