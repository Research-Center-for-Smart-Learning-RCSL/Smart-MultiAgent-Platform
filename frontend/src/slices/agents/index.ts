import { registerLocaleLoaders } from '@shared/i18n'

export { agentsRoutes } from './routes'
export { agentKeys } from './queries'
export { agentsApi, GRAPHRAG_IN_PROGRESS } from './api'
export type {
  Agent,
  RagConfig,
  GraphragConfig,
  GraphragStatus,
  GraphragBuildState,
  ConceptMapOwnerKind,
  ConceptMapOwnerOption,
  AgentConceptMapCoverage,
  AgentConceptMapCoverageEntry,
} from './api'
export { useRagConfigSocket, type RagIngestionProgress } from './composables/useRagConfigSocket'
// Generalized Concept Map surface (Phase 4α) — reused by the owner panels in the
// agent-groups and conversation slices via this barrel (Q-3 / R24.06).
export { default as ConceptMapPanel } from './components/ConceptMapPanel.vue'
export { useGraphragSocket } from './composables/useGraphragSocket'
export {
  graphragBuildStateVariant,
  graphragBuildStateLabelKey,
} from './lib/graphragBuildState'
export {
  agentCreateSchema,
  ragConfigCreateSchema,
  graphragConfigCreateSchema,
  graphragConfigPatchSchema,
  CONCEPT_MAP_OWNER_KINDS,
  RECENCY_HALF_LIFE_MAX_DAYS,
} from './types/schemas'
export type {
  AgentCreateInput,
  RagConfigCreateInput,
  GraphragConfigCreateInput,
  GraphragConfigPatchInput,
} from './types/schemas'

export function installAgentsSlice(): void {
  registerLocaleLoaders({
    en: () => import('./locales/en.json'),
    'zh-TW': () => import('./locales/zh-TW.json'),
  })
}
