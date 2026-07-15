import {
  AgentsService,
  AgentWorkspaceService,
  GraphragService,
  KnowmapService,
  McpService,
  RagService,
} from '@shared/api-client'
import { asBinaryFormField } from '@shared/transport'
import type {
  AgentToolCreateIn,
  AgentToolOut,
  AgentToolTestOut,
  GraphRagConfigPatchIn,
  KnowmapConfigPatchIn,
} from '@shared/api-client'
import type {
  AgentCreateInput,
  AgentToolCreateInput,
  GraphragConfigCreateInput,
  GraphragConfigPatchInput,
  KnowmapConfigCreateInput,
  KnowmapConfigPatchInput,
  RagConfigCreateInput,
} from '../types/schemas'

// Mirrors backend `AgentOut` (backend/app/api/v1/agents.py). `model_hint`
// selects the provider family; the concrete model + key rotation come from the
// bound `key_group_id`.
export interface Agent {
  id: string
  project_id: string
  name: string
  model_hint: string
  model_id: string | null
  effort: string | null
  key_group_id: string
  system_prompt: string
  prompt_strategy: string
  rag_config_id: string | null
  knowmap_config_id: string | null
  context_mode: string
  context_token_cap: number | null
  temperature: number | null
  top_p: number | null
  seed: number | null
  a2a_enabled: boolean
  wakeup_config: Record<string, unknown>
  workflow_capabilities: Record<string, unknown>
  version: number
  created_at: string
  deleted_at: string | null
}

// Mirrors backend `RagDocumentOut`. `agent_ids` is the strict per-agent
// allowlist: only listed agents may retrieve the document's chunks (empty =
// none). Only agents bound to the parent config may appear here.
export interface RagDocument {
  id: string
  rag_config_id: string
  filename: string
  mime: string
  size_bytes: number
  status: 'ingesting' | 'ready' | 'failed' | 'quarantined'
  scan_status: 'pending' | 'clean' | 'quarantined' | 'skipped'
  uploaded_at: string
  agent_ids: string[]
}

// Files at or below this size go through the synchronous multipart endpoint;
// larger ones MUST use the resumable tus path (R22.15 / backend MAX_MULTIPART).
export const RAG_MULTIPART_MAX = 32 * 1024 * 1024

// Mirrors backend `RagConfigOut`.
export interface RagConfig {
  id: string
  project_id: string
  name: string
  chunk_strategy: string
  chunk_params: Record<string, unknown>
  embed_key_id: string | null
  embed_provider: string
  embed_model: string
  rerank_enabled: boolean
  rerank_key_id: string | null
  rerank_provider: string | null
  rerank_model: string | null
  top_k: number
  created_at: string
}

// The backend 2PC build state machine (BuildState). A closed set — kept as a
// union so the badge/variant maps and socket logic stay exhaustive instead of
// falling through on a typo (audit L8).
export type GraphragBuildState =
  | 'idle'
  | 'running'
  | 'neo4j_committed'
  | 'qdrant_committed'
  | 'failed_compensating'
  | 'failed'

// States that mean a build is still moving — anything else is terminal. Single
// source of truth shared by the list view and the live socket (audit M14).
export const GRAPHRAG_IN_PROGRESS: ReadonlySet<GraphragBuildState> = new Set([
  'running',
  'neo4j_committed',
  'failed_compensating',
])

// The three Concept Map owner layers (Phase 4α). Mirrors backend `OwnerKind`.
export type ConceptMapOwnerKind = 'agent_group' | 'chatroom' | 'workspace'

// Mirrors backend `GraphRagConfigOut` (Phase 4α owner-centric). A Concept Map is
// owned by one discriminated owner (`owner_kind` + `owner_id`); `agent_id` is a
// derived convenience (the sole member of a singleton agent_group, else null).
// `builder_key_group_id` extracts triples and MUST differ from the owner's
// consumer key group. `recency_half_life_days` (R11.21) is the temporal decay
// window; null inherits the platform default.
export interface GraphragConfig {
  id: string
  project_id: string
  owner_kind: ConceptMapOwnerKind
  owner_id: string
  // Owner display name (Phase 4α) for the overview; null if the owner was
  // concurrently deleted between the config read and the name resolution.
  owner_name: string | null
  agent_id: string | null
  builder_key_group_id: string
  trigger_config: Record<string, unknown>
  recency_half_life_days: number | null
  last_build_state: GraphragBuildState
  last_build_at: string | null
  last_build_error: string | null
  created_at: string
  deleted_at: string | null
}

// Mirrors backend `ConceptMapOwnerOptionOut` — a selectable owner (without a map
// yet) for the overview create picker (Phase 4α).
export interface ConceptMapOwnerOption {
  owner_kind: ConceptMapOwnerKind
  owner_id: string
  owner_name: string
}

// One Concept Map covering an agent (Phase 4α R11.09) — mirrors backend
// `AgentConceptMapCoverageEntryOut`. Read-only transparency: `active` is whether
// the map currently feeds the agent's retrieval (a chatroom map always does; a
// wide agent_group/workspace map only when its owner enabled it).
export interface AgentConceptMapCoverageEntry {
  config_id: string
  owner_kind: ConceptMapOwnerKind
  owner_id: string
  owner_name: string
  active: boolean
  last_build_state: GraphragBuildState
  last_build_at: string | null
  last_build_error: string | null
}

export interface AgentConceptMapCoverage {
  agent_id: string
  entries: AgentConceptMapCoverageEntry[]
}

// Mirrors backend `GraphRagStatusOut`.
export interface GraphragStatus {
  id: string
  state: GraphragBuildState
  last_build_at: string | null
  last_build_error: string | null
}

// Mirrors backend `GraphNodeOut` / `GraphEdgeOut` / `GraphOut` (viz P0).
export interface GraphNode {
  id: string
  degree: number
  build_id: string | null
  // Coarse entity category for colouring (person/organization/... or '' = unknown).
  type: string
}

export interface GraphEdge {
  source: string
  relation: string
  target: string
  confidence: number
}

export interface GraphView {
  config_id: string
  nodes: GraphNode[]
  edges: GraphEdge[]
  truncated: boolean
}

// Mirrors backend `GraphRagBuildOut` (202 on build accept).
export interface GraphragBuild {
  accepted: boolean
  build_id: string | null
  state: string
}

// --- Knowledge Map (Phase 3β/4β; R11.12/R11.14/R11.23/R11.24) ---

// Mirrors backend `KnowmapConfigOut`. A Knowledge Map is a designer-authored
// Graph RAG built from uploaded documents (not conversation) — project-scoped,
// with a per-document per-agent allowlist. `chunk_strategy` is immutable
// post-creation, same as file-RAG; the embedding provider/model/dim are
// resolved server-side from `builder_key_group_id`, same as GraphRAG (no
// separate embed key selection here).
export interface KnowmapConfig {
  id: string
  project_id: string
  name: string
  builder_key_group_id: string
  chunk_strategy: string
  chunk_params: Record<string, unknown>
  embed_provider: string | null
  embed_model: string | null
  embed_dim: number | null
  last_build_state: GraphragBuildState
  last_build_at: string | null
  last_build_error: string | null
  created_at: string
  deleted_at: string | null
}

// Mirrors backend `KnowmapConfigPatchOut` (R11.25 / F-14): the patched config
// plus any agents auto-detached because the new builder key group collided with
// their consumer group. Empty on a non-colliding change.
export interface KnowmapConfigPatchResult extends KnowmapConfig {
  detached_agent_ids: string[]
}

// Mirrors backend `KnowmapDocumentOut`. `agent_ids` is the strict per-agent
// allowlist (R11.23): a relation is visible to an agent only if every one of
// its source documents is in that agent's allowlist. Empty = no agent may
// retrieve anything derived from this document (secure-by-default).
export interface KnowmapDocument {
  id: string
  knowmap_config_id: string
  filename: string
  mime: string
  size_bytes: number
  status: 'ingesting' | 'ready' | 'failed' | 'quarantined'
  scan_status: 'pending' | 'clean' | 'quarantined' | 'skipped'
  uploaded_at: string
  agent_ids: string[]
}

// Mirrors backend `KnowmapRebuildAck` (202 on rebuild accept). Shape differs
// from `GraphragBuild` (no `accepted`/`state` fields) — matches the backend's
// actual response, not GraphRAG's.
export interface KnowmapRebuildAck {
  status: 'enqueued'
  config_id: string
}

// Mirrors backend `RagConfigPatchIn`. Embedding provider/model/key and the
// chunk strategy are immutable post-creation (an indexed corpus can't switch
// embedding space), so only these fields are patchable.
export interface RagConfigPatchInput {
  name?: string
  top_k?: number
  chunk_params?: Record<string, unknown>
  rerank_enabled?: boolean
  rerank_key_id?: string | null
  // 'cohere' is BYO-key; 'bge' is the keyless bundled local reranker (F-19).
  rerank_provider?: 'cohere' | 'bge' | null
  rerank_model?: string | null
}

// --- Unified Agent Tools (Phase A / Phase B) ---

export type AgentToolType =
  | 'hosted_mcp'
  | 'hosted_web_search'
  | 'hosted_code_interpreter'
  | 'hosted_file_workspace'
  | 'hosted_file_search'
  | 'local_function'
  | 'local_shell'

export interface AgentTool {
  id: string
  agent_id: string
  tool_type: AgentToolType
  enabled: boolean
  display_name: string | null
  config: Record<string, unknown>
  config_warnings: string[]
  created_at: string
}

export interface AgentToolPatchInput {
  enabled?: boolean
  display_name?: string | null
  config?: Record<string, unknown>
  auth?: Record<string, unknown> | null
  // Explicitly remove a stored credential (auth omitted means "leave unchanged").
  clear_auth?: boolean
}

export interface ToolTestResult {
  ok: boolean
  tool_names: string[]
  duration_ms: number
  error: string | null
  // HTTP status from a local_function reachability probe (null for MCP tests).
  status?: number | null
}

// Mirrors backend `WorkspaceFileOut` — designer-uploaded file for Code Interpreter.
export interface WorkspaceFile {
  id: string
  agent_id: string
  path: string
  size_bytes: number
  mime: string
  created_at: string
}

// Mirrors backend `AllowlistEntryOut`.
export interface EgressAllowlistEntry {
  id: string
  project_id: string
  hostname: string
  added_by_user_id: string | null
  added_at: string
  note: string | null
}

// Response bridges — the only generated `*Out` models not directly assignable to
// the slice's hand-rolled types (surfaced by pnpm typecheck at the api boundary):
// AgentToolOut.config_warnings and AgentToolTestOut.error are optional in the
// contract but always emitted; the slice types them required. (RagDocumentOut needed
// a bridge until its status/scan_status were enum-swept server-side to match
// KnowmapDocumentOut — it is now directly assignable, so no bridge.)
function toAgentTool(o: AgentToolOut): AgentTool {
  return { ...o, config_warnings: o.config_warnings ?? [] }
}
function toToolTestResult(o: AgentToolTestOut): ToolTestResult {
  return { ...o, error: o.error ?? null }
}

// Thin wrappers over the generated services (R24.13). Auth and problem+json
// error typing come from shared/transport/axios.ts's instrumentation of the bare
// axios singleton the generated client calls into; each method resolves the
// response body directly (the generated `*Out` models are assignable to the
// slice's hand-rolled types after the response-enum sweep, save the three bridges
// above). The multipart uploads pass the File through the generated formData
// object (the binary field is typed `string` by the codegen; the request core
// builds the FormData and sets the multipart Content-Type).
export const agentsApi = {
  list: (projectId: string): Promise<Agent[]> =>
    AgentsService.listProjectAgentsApiProjectsProjectIdAgentsGet({ projectId }),

  create: (projectId: string, payload: AgentCreateInput): Promise<Agent> =>
    AgentsService.createAgentApiProjectsProjectIdAgentsPost({ projectId, requestBody: payload }),

  get: (agentId: string): Promise<Agent> =>
    AgentsService.readAgentApiAgentsAgentIdGet({ agentId }),

  patch: (agentId: string, version: number, payload: Partial<AgentCreateInput>): Promise<Agent> =>
    AgentsService.patchAgentApiAgentsAgentIdPatch({
      agentId,
      ifMatch: String(version),
      requestBody: payload,
    }),

  remove: (agentId: string, version: number): Promise<void> =>
    AgentsService.deleteAgentApiAgentsAgentIdDelete({ agentId, ifMatch: String(version) }),

  listRagConfigs: (projectId: string): Promise<RagConfig[]> =>
    RagService.listRagConfigsApiProjectsProjectIdRagConfigsGet({ projectId }),

  createRagConfig: (projectId: string, payload: RagConfigCreateInput): Promise<RagConfig> =>
    RagService.createRagConfigApiProjectsProjectIdRagConfigsPost({ projectId, requestBody: payload }),

  deleteRagConfig: (configId: string): Promise<void> =>
    RagService.deleteRagConfigApiRagConfigsConfigIdDelete({ configId }),

  getRagConfig: (configId: string): Promise<RagConfig> =>
    RagService.readRagConfigApiRagConfigsConfigIdGet({ configId }),

  patchRagConfig: (configId: string, payload: RagConfigPatchInput): Promise<RagConfig> =>
    RagService.patchRagConfigApiRagConfigsConfigIdPatch({ configId, requestBody: payload }),

  listDocuments: (configId: string): Promise<RagDocument[]> =>
    RagService.listDocumentsApiRagConfigsConfigIdDocumentsGet({ configId }),

  // ≤ 32 MB synchronous path. Larger files use tusUpload(purpose:'rag_source')
  // from @shared/transport, which the backend routes to the ingest worker.
  // `agentIds` is the per-agent allowlist applied to the new document.
  uploadDocumentMultipart: (configId: string, file: File, agentIds: string[] = []): Promise<RagDocument> =>
    RagService.uploadDocumentApiRagConfigsConfigIdDocumentsPost({
      configId,
      formData: {
        file: asBinaryFormField(file),
        mime: file.type || 'application/octet-stream',
        agent_ids: agentIds,
      },
    }),

  deleteDocument: (documentId: string): Promise<void> =>
    RagService.deleteRagDocumentApiRagDocumentsDocumentIdDelete({ documentId }),

  // Replace a document's per-agent allowlist (empty = no agent may retrieve it).
  setDocumentAgents: (documentId: string, agentIds: string[]): Promise<RagDocument> =>
    RagService.setDocumentAgentsApiRagDocumentsDocumentIdAgentsPatch({
      documentId,
      requestBody: { agent_ids: agentIds },
    }),

  listGraphragConfigs: (projectId: string): Promise<GraphragConfig[]> =>
    GraphragService.listConfigsApiProjectsProjectIdGraphragConfigsGet({ projectId }),

  createGraphragConfig: (projectId: string, payload: GraphragConfigCreateInput): Promise<GraphragConfig> =>
    GraphragService.createConfigApiProjectsProjectIdGraphragConfigsPost({ projectId, requestBody: payload }),

  // Owners in the project without a Concept Map yet — for the overview create
  // picker (Phase 4α); the agents slice can't fetch these entities cross-slice.
  listConceptMapOwnerOptions: (projectId: string): Promise<ConceptMapOwnerOption[]> =>
    GraphragService.listOwnerOptionsApiProjectsProjectIdGraphragConfigsOwnerOptionsGet({ projectId }),

  getGraphragConfig: (configId: string): Promise<GraphragConfig> =>
    GraphragService.readConfigApiGraphragConfigIdGet({ configId }),

  patchGraphragConfig: (configId: string, payload: GraphragConfigPatchInput): Promise<GraphragConfig> =>
    GraphragService.updateConfigApiGraphragConfigIdPatch({
      configId,
      // The zod-inferred optionals are `T | undefined`; the generated In types them
      // `T | null` (both mean "omit" on the wire). Cast bridges exactOptionalPropertyTypes.
      requestBody: payload as GraphRagConfigPatchIn,
    }),

  // Read-only Concept Map coverage for an agent (Phase 4α R11.09) — the maps of
  // its rooms, member groups, and those rooms' workspaces, each flagged active.
  getAgentConceptMapCoverage: (agentId: string): Promise<AgentConceptMapCoverage> =>
    GraphragService.readAgentConceptMapCoverageApiAgentsAgentIdConceptMapCoverageGet({ agentId }),

  getGraphragStatus: (configId: string): Promise<GraphragStatus> =>
    GraphragService.readStatusApiGraphragConfigIdStatusGet({ configId }),

  deleteGraphragConfig: (configId: string): Promise<void> =>
    GraphragService.deleteConfigApiGraphragConfigIdDelete({ configId }),

  buildGraphrag: (configId: string): Promise<GraphragBuild> =>
    GraphragService.triggerBuildApiGraphragConfigIdBuildPost({ configId }),

  getGraphragGraph: (configId: string, limit = 500): Promise<GraphView> =>
    GraphragService.readGraphApiGraphragConfigIdGraphGet({ configId, limit }),

  // --- Knowledge Map (Phase 3β/4β) ---

  listKnowmapConfigs: (projectId: string): Promise<KnowmapConfig[]> =>
    KnowmapService.listKnowmapConfigsApiProjectsProjectIdKnowmapConfigsGet({ projectId }),

  createKnowmapConfig: (projectId: string, payload: KnowmapConfigCreateInput): Promise<KnowmapConfig> =>
    KnowmapService.createKnowmapConfigApiProjectsProjectIdKnowmapConfigsPost({ projectId, requestBody: payload }),

  getKnowmapConfig: (configId: string): Promise<KnowmapConfig> =>
    KnowmapService.readKnowmapConfigApiKnowmapConfigsConfigIdGet({ configId }),

  patchKnowmapConfig: (
    configId: string,
    payload: KnowmapConfigPatchInput,
  ): Promise<KnowmapConfigPatchResult> =>
    KnowmapService.patchKnowmapConfigApiKnowmapConfigsConfigIdPatch({
      configId,
      requestBody: payload as KnowmapConfigPatchIn,
    }) as Promise<KnowmapConfigPatchResult>,

  deleteKnowmapConfig: (configId: string): Promise<void> =>
    KnowmapService.deleteKnowmapConfigApiKnowmapConfigsConfigIdDelete({ configId }),

  rebuildKnowmap: (configId: string): Promise<KnowmapRebuildAck> =>
    KnowmapService.rebuildKnowmapConfigApiKnowmapConfigsConfigIdRebuildPost({ configId }),

  // Same GraphView shape as GraphRAG (backend KnowmapGraphOut is field-for-
  // field identical to GraphOut) — reused directly, no separate type.
  getKnowmapGraph: (configId: string, limit = 500): Promise<GraphView> =>
    KnowmapService.readKnowmapGraphApiKnowmapConfigsConfigIdGraphGet({ configId, limit }),

  listKnowmapDocuments: (configId: string): Promise<KnowmapDocument[]> =>
    KnowmapService.listKnowmapDocumentsApiKnowmapConfigsConfigIdDocumentsGet({ configId }),

  // ≤ 32 MB synchronous path. Larger files use tusUpload(purpose:'knowmap_source')
  // from @shared/transport. `agentIds` is the per-agent allowlist applied to
  // the new document (R11.23).
  uploadKnowmapDocumentMultipart: (configId: string, file: File, agentIds: string[] = []): Promise<KnowmapDocument> =>
    KnowmapService.uploadKnowmapDocumentApiKnowmapConfigsConfigIdDocumentsPost({
      configId,
      formData: {
        file: asBinaryFormField(file),
        mime: file.type || 'application/octet-stream',
        agent_ids: agentIds,
      },
    }),

  deleteKnowmapDocument: (documentId: string): Promise<void> =>
    KnowmapService.deleteKnowmapDocumentApiKnowmapDocumentsDocumentIdDelete({ documentId }),

  // Replace a document's per-agent allowlist (empty = no agent may retrieve it).
  setKnowmapDocumentAgents: (documentId: string, agentIds: string[]): Promise<KnowmapDocument> =>
    KnowmapService.setKnowmapDocumentAgentsApiKnowmapDocumentsDocumentIdAgentsPatch({
      documentId,
      requestBody: { agent_ids: agentIds },
    }),

  listEgressAllowlist: (projectId: string): Promise<EgressAllowlistEntry[]> =>
    McpService.listAllowlistApiProjectsProjectIdMcpEgressAllowlistGet({ projectId }),

  addEgressAllowlistEntry: (
    projectId: string,
    payload: { hostname: string; note: string | null },
  ): Promise<EgressAllowlistEntry> =>
    McpService.addAllowlistEntryApiProjectsProjectIdMcpEgressAllowlistPost({
      projectId,
      requestBody: payload,
    }),

  // The generated client path-encodes the hostname (encodeURI, not the old
  // encodeURIComponent) — equivalent for the DNS-valid hostnames the backend stores,
  // since neither escapes [a-z0-9.-*]. Revisit if hostname validation ever loosens.
  removeEgressAllowlistEntry: (projectId: string, hostname: string): Promise<void> =>
    McpService.removeAllowlistEntryApiProjectsProjectIdMcpEgressAllowlistHostnameDelete({
      projectId,
      hostname,
    }),

  // --- Unified Tools API (Phase A) ---

  listTools: (agentId: string): Promise<AgentTool[]> =>
    AgentsService.listAgentToolsApiAgentsAgentIdToolsGet({ agentId }).then((r) =>
      r.map(toAgentTool),
    ),

  addTool: (agentId: string, payload: AgentToolCreateInput): Promise<AgentTool> =>
    AgentsService.addAgentToolApiAgentsAgentIdToolsPost({
      agentId,
      requestBody: payload as AgentToolCreateIn,
    }).then(toAgentTool),

  patchTool: (agentId: string, toolId: string, payload: AgentToolPatchInput): Promise<AgentTool> =>
    AgentsService.patchAgentToolApiAgentsAgentIdToolsToolIdPatch({
      agentId,
      toolId,
      requestBody: payload,
    }).then(toAgentTool),

  deleteTool: (agentId: string, toolId: string): Promise<void> =>
    AgentsService.deleteAgentToolApiAgentsAgentIdToolsToolIdDelete({ agentId, toolId }),

  testTool: (agentId: string, toolId: string): Promise<ToolTestResult> =>
    AgentsService.testAgentToolApiAgentsAgentIdToolsToolIdTestPost({ agentId, toolId }).then(
      toToolTestResult,
    ),

  // --- Workspace Files (Phase D) ---

  listWorkspaceFiles: (agentId: string): Promise<WorkspaceFile[]> =>
    AgentWorkspaceService.listWorkspaceFilesApiAgentsAgentIdWorkspaceFilesGet({ agentId }),

  uploadWorkspaceFile: (agentId: string, file: File, path?: string): Promise<WorkspaceFile> =>
    AgentWorkspaceService.uploadWorkspaceFileApiAgentsAgentIdWorkspaceFilesPost({
      agentId,
      formData: { file: asBinaryFormField(file), ...(path ? { path } : {}) },
    }),

  deleteWorkspaceFile: (agentId: string, fileId: string): Promise<void> =>
    AgentWorkspaceService.deleteWorkspaceFileApiAgentsAgentIdWorkspaceFilesFileIdDelete({ agentId, fileId }),
}
