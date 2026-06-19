// Wakeup config validation / normalisation utilities.
//
// WakeupConfigEditor dereferences `triggers.{every_n_messages,silence_minutes,
// call_only}.enabled` at setup, so it only accepts a fully-formed config — a
// partial one (e.g. `{triggers:{}}`) would crash the whole panel. These helpers
// validate the three trigger sub-objects are present before handing it over.
//
// Extracted from ChatroomSettingsView.vue (M22 SoC fix — wakeup is workflow
// domain knowledge, not conversation domain).

import type { WakeupConfig } from '@shared/types/workflow'

/** Returns true if `raw` has the three required trigger sub-objects. */
export function isFullWakeupConfig(raw: unknown): boolean {
  if (!raw || typeof raw !== 'object') return false
  const triggers = (raw as Record<string, unknown>).triggers
  if (!triggers || typeof triggers !== 'object') return false
  const t = triggers as Record<string, unknown>
  return (
    typeof t.every_n_messages === 'object'
    && typeof t.silence_minutes === 'object'
    && typeof t.call_only === 'object'
  )
}

/**
 * Return a plain deep clone when the shape is valid, else undefined so the
 * editor stays hidden. The source is a reactive proxy and the editor
 * structuredClones its model-value (which rejects proxies), so the JSON
 * round-trip both unwraps and deep-copies.
 */
export function toEditableWakeup(raw: unknown): WakeupConfig | undefined {
  return isFullWakeupConfig(raw)
    ? (JSON.parse(JSON.stringify(raw)) as WakeupConfig)
    : undefined
}
