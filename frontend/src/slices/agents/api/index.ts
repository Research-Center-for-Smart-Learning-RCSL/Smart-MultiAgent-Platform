import { http } from '@shared/transport'
import type {
  AgentCreateInput,
  AgentToolCreateInput,
  GraphragConfigCreateInput,
  McpBindingCreateInput,
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
  key_group_id: string
  system_prompt: string
  prompt_strategy: string
  rag_config_id: string | null
  graphrag_config_id: string | null
  context_mode: string
  context_token_cap: number | null
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

// Mirrors backend `GraphRagConfigOut`. A GraphRAG config is 1:1 with an agent
// (R15.16); `builder_key_group_id` is the key group used to extract triples and
// MUST differ from the agent's own consumer key group (billing separation).
export interface GraphragConfig {
  id: string
  project_id: string
  agent_id: string
  builder_key_group_id: string
  trigger_config: Record<string, unknown>
  last_build_state: string
  last_build_at: string | null
  last_build_error: string | null
  created_at: string
  deleted_at: string | null
}

// Mirrors backend `GraphRagStatusOut`.
export interface GraphragStatus {
  id: string
  state: string
  last_build_at: string | null
  last_build_error: string | null
}

// Mirrors backend `GraphRagBuildOut` (202 on build accept).
export interface GraphragBuild {
  accepted: boolean
  build_id: string | null
  state: string
}

// Mirrors backend `McpBindingOut`. `source` selects how `reference` is
// interpreted: a built-in tool name, an MCP server URL, or a package spec.
// `allowed_tools` whitelists which of the server's tools the agent may call.
export interface McpBinding {
  id: string
  agent_id: string
  source: 'builtin' | 'url' | 'package'
  reference: string
  allowed_tools: string[]
  config: Record<string, unknown>
  created_at: string
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
  rerank_provider?: 'cohere' | null
  rerank_model?: string | null
}

// Mirrors backend `McpBindingPatchIn`. `source` and `reference` are immutable;
// only the tool allowlist and advanced config may be edited.
export interface McpBindingPatchInput {
  allowed_tools?: string[]
  config?: Record<string, unknown>
}

// Built-in tool gating is owned server-side; the editor reads/writes the
// enabled set and never re-derives the gate rule. Mirrors `BuiltinToolsOut`.
export interface BuiltinToolsState {
  enabled: string[]
}

// Mirrors backend `McpTestOut` — the sandbox probe result.
export interface McpTestResult {
  ok: boolean
  tool_names: string[]
  duration_ms: number
  error: string | null
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
}

export interface ToolTestResult {
  ok: boolean
  tool_names: string[]
  duration_ms: number
  error: string | null
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

export const agentsApi = {
  list: (projectId: string) =>
    http.get<Agent[]>(`/projects/${projectId}/agents`),

  create: (projectId: string, payload: AgentCreateInput) =>
    http.post<Agent>(`/projects/${projectId}/agents`, payload),

  get: (agentId: string) =>
    http.get<Agent>(`/agents/${agentId}`),

  patch: (agentId: string, version: number, payload: Partial<AgentCreateInput>) =>
    http.patch<Agent>(`/agents/${agentId}`, payload, {
      headers: { 'If-Match': String(version) },
    }),

  remove: (agentId: string, version: number) =>
    http.delete(`/agents/${agentId}`, {
      headers: { 'If-Match': String(version) },
    }),

  listRagConfigs: (projectId: string) =>
    http.get<RagConfig[]>(`/projects/${projectId}/rag-configs`),

  createRagConfig: (projectId: string, payload: RagConfigCreateInput) =>
    http.post<RagConfig>(`/projects/${projectId}/rag-configs`, payload),

  deleteRagConfig: (configId: string) =>
    http.delete(`/rag-configs/${configId}`),

  getRagConfig: (configId: string) =>
    http.get<RagConfig>(`/rag-configs/${configId}`),

  patchRagConfig: (configId: string, payload: RagConfigPatchInput) =>
    http.patch<RagConfig>(`/rag-configs/${configId}`, payload),

  listDocuments: (configId: string) =>
    http.get<RagDocument[]>(`/rag-configs/${configId}/documents`),

  // ≤ 32 MB synchronous path. Larger files use tusUpload(purpose:'rag_source')
  // from @shared/transport, which the backend routes to the ingest worker.
  // `agentIds` is the per-agent allowlist applied to the new document.
  uploadDocumentMultipart: (configId: string, file: File, agentIds: string[] = []) => {
    const form = new FormData()
    form.append('file', file)
    form.append('mime', file.type || 'application/octet-stream')
    for (const id of agentIds) form.append('agent_ids', id)
    return http.post<RagDocument>(`/rag-configs/${configId}/documents`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },

  deleteDocument: (documentId: string) =>
    http.delete(`/rag-documents/${documentId}`),

  // Replace a document's per-agent allowlist (empty = no agent may retrieve it).
  setDocumentAgents: (documentId: string, agentIds: string[]) =>
    http.patch<RagDocument>(`/rag-documents/${documentId}/agents`, { agent_ids: agentIds }),

  listGraphragConfigs: (projectId: string) =>
    http.get<GraphragConfig[]>(`/projects/${projectId}/graphrag-configs`),

  createGraphragConfig: (projectId: string, payload: GraphragConfigCreateInput) =>
    http.post<GraphragConfig>(`/projects/${projectId}/graphrag-configs`, payload),

  getGraphragStatus: (configId: string) =>
    http.get<GraphragStatus>(`/graphrag/${configId}/status`),

  deleteGraphragConfig: (configId: string) =>
    http.delete(`/graphrag/${configId}`),

  buildGraphrag: (configId: string) =>
    http.post<GraphragBuild>(`/graphrag/${configId}/build`),

  listMcpBindings: (agentId: string) =>
    http.get<McpBinding[]>(`/agents/${agentId}/mcp`),

  addMcpBinding: (agentId: string, payload: McpBindingCreateInput) =>
    http.post<McpBinding>(`/agents/${agentId}/mcp`, payload),

  patchMcpBinding: (agentId: string, bindingId: string, payload: McpBindingPatchInput) =>
    http.patch<McpBinding>(`/agents/${agentId}/mcp/${bindingId}`, payload),

  deleteMcpBinding: (agentId: string, bindingId: string) =>
    http.delete(`/agents/${agentId}/mcp/${bindingId}`),

  testMcpBinding: (agentId: string, bindingId: string) =>
    http.post<McpTestResult>(`/agents/${agentId}/mcp/${bindingId}/test`),

  getBuiltinTools: (agentId: string) =>
    http.get<BuiltinToolsState>(`/agents/${agentId}/builtin-tools`),

  setBuiltinTools: (agentId: string, enabled: string[]) =>
    http.put<BuiltinToolsState>(`/agents/${agentId}/builtin-tools`, { enabled }),

  listEgressAllowlist: (projectId: string) =>
    http.get<EgressAllowlistEntry[]>(`/projects/${projectId}/mcp/egress-allowlist`),

  addEgressAllowlistEntry: (
    projectId: string,
    payload: { hostname: string; note: string | null },
  ) => http.post<EgressAllowlistEntry>(`/projects/${projectId}/mcp/egress-allowlist`, payload),

  removeEgressAllowlistEntry: (projectId: string, hostname: string) =>
    http.delete(`/projects/${projectId}/mcp/egress-allowlist/${encodeURIComponent(hostname)}`),

  // --- Unified Tools API (Phase A) ---

  listTools: (agentId: string) =>
    http.get<AgentTool[]>(`/agents/${agentId}/tools`),

  addTool: (agentId: string, payload: AgentToolCreateInput) =>
    http.post<AgentTool>(`/agents/${agentId}/tools`, payload),

  patchTool: (agentId: string, toolId: string, payload: AgentToolPatchInput) =>
    http.patch<AgentTool>(`/agents/${agentId}/tools/${toolId}`, payload),

  deleteTool: (agentId: string, toolId: string) =>
    http.delete(`/agents/${agentId}/tools/${toolId}`),

  testTool: (agentId: string, toolId: string) =>
    http.post<ToolTestResult>(`/agents/${agentId}/tools/${toolId}/test`),

  // --- Workspace Files (Phase D) ---

  listWorkspaceFiles: (agentId: string) =>
    http.get<WorkspaceFile[]>(`/agents/${agentId}/workspace-files`),

  uploadWorkspaceFile: (agentId: string, file: File, path?: string) => {
    const form = new FormData()
    form.append('file', file)
    if (path) form.append('path', path)
    return http.post<WorkspaceFile>(`/agents/${agentId}/workspace-files`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },

  deleteWorkspaceFile: (agentId: string, fileId: string) =>
    http.delete(`/agents/${agentId}/workspace-files/${fileId}`),
}
