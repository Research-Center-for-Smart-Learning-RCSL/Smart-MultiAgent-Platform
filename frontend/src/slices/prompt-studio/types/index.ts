// Types mirroring the backend prompt_studio contract (§29). Hand-written to
// match `backend/app/api/v1/prompt_studio.py`; the generated api-client is
// drift-check-only and not imported at runtime.

export type PromptScope = 'platform' | 'org' | 'user'

export type ScanStatus = 'pending' | 'clean' | 'infected' | 'error'

export interface KeyMeta {
  id: string
  provider: string
  name: string
  masked_preview: string
}

export interface AssistantFile {
  id: string
  filename: string
  size_bytes: number
  mime: string
  scan_status: ScanStatus
  extracted_chars: number
  created_at: string
}

export interface AssistantConfig {
  scope: PromptScope
  enabled: boolean
  system_prompt: string
  key_id: string | null
  key: KeyMeta | null
  key_revoked: boolean
  model_id: string | null
  daily_request_limit_per_user: number
  hide_platform_templates: boolean
  version: number
  files: AssistantFile[]
}

export interface ConfigEnvelope {
  configured: boolean
  config: AssistantConfig | null
}

export interface AssistantConfigPutInput {
  system_prompt: string
  key_id: string | null
  model_id: string | null
  daily_request_limit_per_user: number
  enabled: boolean
  hide_platform_templates: boolean
}

export interface PromptTemplate {
  id: string
  scope: PromptScope
  name: string
  description: string
  body: string
  position: number
  version: number
  created_at: string
  updated_at: string
}

export interface TemplateCreateInput {
  name: string
  description: string
  body: string
}

export interface TemplatePatchInput {
  name?: string
  description?: string
  body?: string
  position?: number
}

export interface ResolvedAssistant {
  available: boolean
  source_scope: PromptScope | null
  model_id: string | null
}

export interface SessionCreated {
  session_id: string
}

// Mirrors backend ModelCatalogOut (read directly so prompt-studio stays
// independent of the agents slice).
export interface ChatModelProvider {
  provider: string
  models: string[]
  default: string
  context_limit: number
}

export interface ModelCatalog {
  chat: ChatModelProvider[]
}

// Config-owner scope descriptor used by the shared form + api layer to target
// the right endpoint family without the views duplicating URL logic.
export type ConfigScopeRef =
  | { kind: 'user' }
  | { kind: 'org'; orgId: string }
  | { kind: 'platform' }
