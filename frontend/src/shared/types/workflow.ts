// Cross-cutting orchestration / wakeup types consumed by multiple slices
// (conversation, workflow, agents).  Moved here from the workflow slice to
// break the conversation -> workflow type coupling (H15).

import { deepCloneJSON } from '@shared/utils/deepClone'

// ---------------------------------------------------------------------------
// Approval DTOs (G.6–G.8)
// ---------------------------------------------------------------------------

export type ApprovalMode = 'single' | 'majority' | 'consensus'
export type ApprovalState = 'pending' | 'approved' | 'rejected' | 'timeout_leader'

export interface Approval {
  id: string
  workflow_run_id: string
  mode: ApprovalMode
  leader_agent_id: string
  approver_agent_ids: string[]
  timeout_seconds: number
  state: ApprovalState
  started_at: string
  ended_at: string | null
}

export interface ApprovalVote {
  approval_id: string
  voter_agent_id: string
  vote: boolean
  rationale: string | null
  cast_at: string
}

export interface ApprovalWithVotes extends Approval {
  votes: ApprovalVote[]
}

// ---------------------------------------------------------------------------
// Wakeup / DLQ DTOs
// ---------------------------------------------------------------------------

export interface WakeupTriggerConfig {
  enabled: boolean
  [k: string]: unknown
}

export interface WakeupConfig {
  triggers: {
    every_n_messages: WakeupTriggerConfig & { n: number }
    silence_minutes: WakeupTriggerConfig & {
      t_minutes: number
      autostop_rounds: number
      autostop_max_default: number
      observer_autostop_rounds: number
    }
    call_only: WakeupTriggerConfig
  }
  allow_self_open: boolean
  refresh_every_hours: number
  /**
   * Designer-set per-agent bounds (R15.08). Admin-set through the API; there is
   * no editor control, so this module only carries it through.
   */
  soft_bounds?: Record<string, unknown>
  /**
   * The column is free-form on the server, so a designer may have written keys
   * no version of this module knows about. Mirrors the index signature on
   * `WakeupTriggerConfig` one level down.
   */
  [k: string]: unknown
}

// Canonical fully-populated wakeup config. Most agents persist a partial (or
// empty) wakeup_config; SWakeupEditor dereferences the nested trigger shape at
// setup and would crash on a partial one, so every consumer must merge defaults
// before handing a config to the editor. The every-N and autostop defaults
// mirror contexts/orchestration/domain/models.py; the 30-minute silence value
// is the editor's deliberate UX default.
const DEFAULT_WAKEUP: WakeupConfig = {
  triggers: {
    every_n_messages: { enabled: false, n: 3 },
    silence_minutes: {
      enabled: false,
      t_minutes: 30,
      autostop_rounds: 5,
      autostop_max_default: 100,
      observer_autostop_rounds: 50,
    },
    call_only: { enabled: false },
  },
  allow_self_open: false,
  refresh_every_hours: 24,
}

/** A fresh, fully-populated default config (safe to mutate). */
export function defaultWakeupConfig(): WakeupConfig {
  return deepCloneJSON(DEFAULT_WAKEUP)
}

function normalizeNestedTriggers(
  tg: Record<string, Record<string, unknown>>,
): WakeupConfig['triggers'] {
  return {
    every_n_messages: { ...DEFAULT_WAKEUP.triggers.every_n_messages, ...(tg.every_n_messages ?? {}) },
    silence_minutes: { ...DEFAULT_WAKEUP.triggers.silence_minutes, ...(tg.silence_minutes ?? {}) },
    call_only: { ...DEFAULT_WAKEUP.triggers.call_only, ...(tg.call_only ?? {}) },
  }
}

// Format written before the nested `triggers` schema (pre commit 4b39447,
// 2026-06-26): fields sat directly on the wakeup_config root instead of under
// `triggers`. Old rows in this shape were never migrated, so this fallback
// keeps their values visible/editable instead of silently resetting them to
// defaults the moment they're opened.
function normalizeLegacyFlatTriggers(r: Record<string, unknown>): WakeupConfig['triggers'] {
  return {
    every_n_messages: {
      ...DEFAULT_WAKEUP.triggers.every_n_messages,
      enabled: !!r.every_n_messages,
      ...(typeof r.every_n_messages === 'number' ? { n: r.every_n_messages } : {}),
    },
    silence_minutes: {
      ...DEFAULT_WAKEUP.triggers.silence_minutes,
      enabled: !!r.silence_minutes,
      ...(typeof r.silence_minutes === 'number' ? { t_minutes: r.silence_minutes } : {}),
      ...(typeof r.autostop_rounds === 'number' ? { autostop_rounds: r.autostop_rounds } : {}),
    },
    call_only: {
      ...DEFAULT_WAKEUP.triggers.call_only,
      enabled: (r.call_only as boolean) ?? false,
    },
  }
}

/**
 * Merge a stored (possibly partial, empty, or legacy-flat) wakeup_config over
 * the defaults so the result always has the three trigger sub-objects the
 * editor requires, and enforce the call_only / autonomous-trigger mutual
 * exclusivity invariant. This is the single normalization funnel every
 * consumer uses, so a contradictory or legacy-shaped stored config is
 * corrected the moment it's loaded rather than silently persisted or
 * discarded.
 */
// Legacy-shape root keys: on that path `normalizeLegacyFlatTriggers` has already
// folded them into `triggers`, so carrying them through as well would re-emit the
// pre-2026-06-26 shape alongside its replacement. The three keys this function
// returns explicitly need no filtering — they are spread last and win.
const LEGACY_ROOT_KEYS = ['every_n_messages', 'silence_minutes', 'call_only', 'autostop_rounds']

export function normalizeWakeupConfig(raw: unknown): WakeupConfig {
  const r = (raw ?? {}) as Record<string, unknown>
  const isLegacy = r.triggers === undefined
  const triggers = isLegacy
    ? normalizeLegacyFlatTriggers(r)
    : normalizeNestedTriggers(r.triggers as Record<string, Record<string, unknown>>)

  if (triggers.call_only.enabled) {
    triggers.every_n_messages.enabled = false
    triggers.silence_minutes.enabled = false
  }

  // Everything this module does not model is a designer-written key (`soft_bounds`,
  // R15.08) that the editor cannot show and must not drop: this result is what the
  // views PATCH back, so a key missing here is a key the designer loses.
  const passthrough = isLegacy
    ? Object.fromEntries(Object.entries(r).filter(([key]) => !LEGACY_ROOT_KEYS.includes(key)))
    : r

  return {
    ...passthrough,
    triggers,
    allow_self_open: (r.allow_self_open as boolean) ?? DEFAULT_WAKEUP.allow_self_open,
    refresh_every_hours: (r.refresh_every_hours as number) ?? DEFAULT_WAKEUP.refresh_every_hours,
  }
}
