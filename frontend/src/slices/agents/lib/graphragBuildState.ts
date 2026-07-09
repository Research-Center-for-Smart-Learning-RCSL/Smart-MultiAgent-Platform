// Shared Concept Map build-state presentation (Phase 4α). Extracted from the
// GraphRAG list view so the generalized overview and the contextual owner panels
// (in agents, agent-groups, conversation) render build state identically.
// Exported via the agents barrel; consumers apply t() to the label key.

import type { GraphragBuildState } from '../api'

type BadgeVariant = 'neutral' | 'info' | 'danger' | 'warning'

const VARIANT_BY_STATE: Record<GraphragBuildState, BadgeVariant> = {
  idle: 'neutral',
  running: 'info',
  neo4j_committed: 'info',
  qdrant_committed: 'info',
  failed: 'danger',
  failed_compensating: 'warning',
}

const LABEL_KEY_BY_STATE: Record<GraphragBuildState, string> = {
  idle: 'agents.graphragList.states.idle',
  running: 'agents.graphragList.states.running',
  neo4j_committed: 'agents.graphragList.states.neo4jCommitted',
  qdrant_committed: 'agents.graphragList.states.qdrantCommitted',
  failed: 'agents.graphragList.states.failed',
  failed_compensating: 'agents.graphragList.states.compensating',
}

export function graphragBuildStateVariant(state: string): BadgeVariant {
  return VARIANT_BY_STATE[state as GraphragBuildState] ?? 'neutral'
}

// Returns an i18n key; the caller resolves it with t(). Falls back to the raw
// state string for an unknown value so nothing renders blank.
export function graphragBuildStateLabelKey(state: string): string {
  return LABEL_KEY_BY_STATE[state as GraphragBuildState] ?? state
}
