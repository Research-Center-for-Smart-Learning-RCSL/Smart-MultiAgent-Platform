import { http } from '@shared/transport'

import type {
  AssistantConfig,
  AssistantConfigPutInput,
  AssistantFile,
  ConfigEnvelope,
  ConfigScopeRef,
  ModelCatalog,
  PromptTemplate,
  ResolvedAssistant,
  SessionCreated,
  TemplateCreateInput,
  TemplatePatchInput,
} from '../types'

function configBase(scope: ConfigScopeRef): string {
  if (scope.kind === 'user') return '/me/prompt-assistant'
  if (scope.kind === 'org') return `/orgs/${scope.orgId}/prompt-assistant`
  return '/admin/prompt-assistant'
}

function templateBase(scope: ConfigScopeRef): string {
  if (scope.kind === 'user') return '/me/prompt-templates'
  if (scope.kind === 'org') return `/orgs/${scope.orgId}/prompt-templates`
  return '/admin/prompt-templates'
}

const ifMatch = (version: number | null) =>
  version === null ? {} : { headers: { 'If-Match': String(version) } }

export const promptStudioApi = {
  // --- config (scoped) ---
  getConfig: (scope: ConfigScopeRef) => http.get<ConfigEnvelope>(`${configBase(scope)}/config`),

  putConfig: (scope: ConfigScopeRef, version: number | null, payload: AssistantConfigPutInput) =>
    http.put<AssistantConfig>(`${configBase(scope)}/config`, payload, ifMatch(version)),

  uploadFile: (scope: ConfigScopeRef, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return http.post<AssistantFile>(`${configBase(scope)}/config/files`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },

  deleteFile: (scope: ConfigScopeRef, fileId: string) =>
    http.delete(`${configBase(scope)}/config/files/${fileId}`),

  // --- templates (scoped CRUD) ---
  listTemplates: (scope: ConfigScopeRef) => http.get<PromptTemplate[]>(templateBase(scope)),

  createTemplate: (scope: ConfigScopeRef, payload: TemplateCreateInput) =>
    http.post<PromptTemplate>(templateBase(scope), payload),

  patchTemplate: (scope: ConfigScopeRef, id: string, version: number, payload: TemplatePatchInput) =>
    http.patch<PromptTemplate>(`${templateBase(scope)}/${id}`, payload, {
      headers: { 'If-Match': String(version) },
    }),

  deleteTemplate: (scope: ConfigScopeRef, id: string) =>
    http.delete(`${templateBase(scope)}/${id}`),

  // --- project-scoped resolved reads ---
  resolvedForProject: (projectId: string) =>
    http.get<ResolvedAssistant>(`/projects/${projectId}/prompt-assistant`),

  mergedTemplates: (projectId: string) =>
    http.get<PromptTemplate[]>(`/projects/${projectId}/prompt-templates`),

  // --- streaming session ---
  createSession: (projectId: string) =>
    http.post<SessionCreated>(`/projects/${projectId}/prompt-assistant/sessions`),

  postMessage: (sessionId: string, payload: { content: string; editor_draft: string | null }) =>
    http.post(`/prompt-assistant/sessions/${sessionId}/messages`, payload),

  // Shared read reused by the config form's model picker.
  getModelCatalog: () => http.get<ModelCatalog>('/model-catalog'),
}

