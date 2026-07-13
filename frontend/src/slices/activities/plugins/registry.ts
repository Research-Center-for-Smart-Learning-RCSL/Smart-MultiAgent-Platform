// First-party plugin registry, keyed by manifest.key (== ActivityType.key). v1
// ships no bundled plugin here; the project's canvas plugin (out of scope for
// this SDK task) registers itself by importing `registerActivityPlugin`.

import type { ActivityPlugin } from '../sdk/types'

const registry = new Map<string, ActivityPlugin>()

export function registerActivityPlugin(plugin: ActivityPlugin): void {
  registry.set(plugin.manifest.key, plugin)
}

export function getActivityPlugin(key: string): ActivityPlugin | undefined {
  return registry.get(key)
}

/** Test/hot-reload aid — clears every registered plugin. */
export function clearActivityPlugins(): void {
  registry.clear()
}
