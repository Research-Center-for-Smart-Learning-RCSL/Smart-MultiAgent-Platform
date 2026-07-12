// Prompt-studio API.
//
// Wraps the generated PromptStudioService (+ ModelCatalogService for the shared model
// catalog) over the one instrumented axios singleton. The methods return the bare body, so
// consumers no longer read `.data`.
//
// Scoped methods dispatch on ConfigScopeRef.kind to the per-scope generated method family
// (user -> me*, org -> org*, platform -> admin*), replacing the old configBase/templateBase
// URL builders. The generated *Out models type the backend enum fields (scan_status, scope,
// source_scope) as `string`; the hand-rolled types narrow them to unions, so the resolved
// body is cast back at the boundary — the same unchecked assertion the previous
// `http.get<T>()` calls made, safe because these are backend-closed enums.

import { ModelCatalogService, PromptStudioService } from '@shared/api-client'
import { asBinaryFormField } from '@shared/transport'

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

// Route a scoped call to the per-scope generated method. `platform` targets the admin*
// endpoints (the backend's platform-wide config), matching the old configBase() fallthrough.
function dispatchScope<T>(
  scope: ConfigScopeRef,
  handlers: { user: () => T; org: (orgId: string) => T; platform: () => T },
): T {
  if (scope.kind === 'user') return handlers.user()
  if (scope.kind === 'org') return handlers.org(scope.orgId)
  return handlers.platform()
}

export const promptStudioApi = {
  // --- config (scoped) ---
  getConfig: (scope: ConfigScopeRef): Promise<ConfigEnvelope> =>
    dispatchScope(scope, {
      user: () => PromptStudioService.meGetConfigApiMePromptAssistantConfigGet(),
      org: (orgId) => PromptStudioService.orgGetConfigApiOrgsOrgIdPromptAssistantConfigGet({ orgId }),
      platform: () => PromptStudioService.adminGetConfigApiAdminPromptAssistantConfigGet(),
    }).then((r) => r as ConfigEnvelope),

  putConfig: (
    scope: ConfigScopeRef,
    version: number | null,
    payload: AssistantConfigPutInput,
  ): Promise<AssistantConfig> => {
    // null version -> no If-Match; the generated request core drops null headers.
    const ifMatch = version === null ? null : String(version)
    return dispatchScope(scope, {
      user: () =>
        PromptStudioService.mePutConfigApiMePromptAssistantConfigPut({ requestBody: payload, ifMatch }),
      org: (orgId) =>
        PromptStudioService.orgPutConfigApiOrgsOrgIdPromptAssistantConfigPut({
          orgId,
          requestBody: payload,
          ifMatch,
        }),
      platform: () =>
        PromptStudioService.adminPutConfigApiAdminPromptAssistantConfigPut({
          requestBody: payload,
          ifMatch,
        }),
    }).then((r) => r as AssistantConfig)
  },

  uploadFile: (scope: ConfigScopeRef, file: File): Promise<AssistantFile> => {
    const formData = { file: asBinaryFormField(file) }
    return dispatchScope(scope, {
      user: () => PromptStudioService.meUploadFileApiMePromptAssistantConfigFilesPost({ formData }),
      org: (orgId) =>
        PromptStudioService.orgUploadFileApiOrgsOrgIdPromptAssistantConfigFilesPost({ orgId, formData }),
      platform: () =>
        PromptStudioService.adminUploadFileApiAdminPromptAssistantConfigFilesPost({ formData }),
    }).then((r) => r as AssistantFile)
  },

  deleteFile: (scope: ConfigScopeRef, fileId: string) =>
    dispatchScope(scope, {
      user: () => PromptStudioService.meDeleteFileApiMePromptAssistantConfigFilesFileIdDelete({ fileId }),
      org: (orgId) =>
        PromptStudioService.orgDeleteFileApiOrgsOrgIdPromptAssistantConfigFilesFileIdDelete({
          orgId,
          fileId,
        }),
      platform: () =>
        PromptStudioService.adminDeleteFileApiAdminPromptAssistantConfigFilesFileIdDelete({ fileId }),
    }),

  // --- templates (scoped CRUD) ---
  listTemplates: (scope: ConfigScopeRef): Promise<PromptTemplate[]> =>
    dispatchScope(scope, {
      user: () => PromptStudioService.meListTemplatesApiMePromptTemplatesGet(),
      org: (orgId) => PromptStudioService.orgListTemplatesApiOrgsOrgIdPromptTemplatesGet({ orgId }),
      platform: () => PromptStudioService.adminListTemplatesApiAdminPromptTemplatesGet(),
    }).then((r) => r as PromptTemplate[]),

  createTemplate: (scope: ConfigScopeRef, payload: TemplateCreateInput): Promise<PromptTemplate> =>
    dispatchScope(scope, {
      user: () => PromptStudioService.meCreateTemplateApiMePromptTemplatesPost({ requestBody: payload }),
      org: (orgId) =>
        PromptStudioService.orgCreateTemplateApiOrgsOrgIdPromptTemplatesPost({ orgId, requestBody: payload }),
      platform: () =>
        PromptStudioService.adminCreateTemplateApiAdminPromptTemplatesPost({ requestBody: payload }),
    }).then((r) => r as PromptTemplate),

  patchTemplate: (
    scope: ConfigScopeRef,
    id: string,
    version: number,
    payload: TemplatePatchInput,
  ): Promise<PromptTemplate> => {
    const ifMatch = String(version)
    return dispatchScope(scope, {
      user: () =>
        PromptStudioService.mePatchTemplateApiMePromptTemplatesTemplateIdPatch({
          templateId: id,
          ifMatch,
          requestBody: payload,
        }),
      org: (orgId) =>
        PromptStudioService.orgPatchTemplateApiOrgsOrgIdPromptTemplatesTemplateIdPatch({
          orgId,
          templateId: id,
          ifMatch,
          requestBody: payload,
        }),
      platform: () =>
        PromptStudioService.adminPatchTemplateApiAdminPromptTemplatesTemplateIdPatch({
          templateId: id,
          ifMatch,
          requestBody: payload,
        }),
    }).then((r) => r as PromptTemplate)
  },

  deleteTemplate: (scope: ConfigScopeRef, id: string) =>
    dispatchScope(scope, {
      user: () => PromptStudioService.meDeleteTemplateApiMePromptTemplatesTemplateIdDelete({ templateId: id }),
      org: (orgId) =>
        PromptStudioService.orgDeleteTemplateApiOrgsOrgIdPromptTemplatesTemplateIdDelete({
          orgId,
          templateId: id,
        }),
      platform: () =>
        PromptStudioService.adminDeleteTemplateApiAdminPromptTemplatesTemplateIdDelete({ templateId: id }),
    }),

  // --- project-scoped resolved reads ---
  resolvedForProject: (projectId: string): Promise<ResolvedAssistant> =>
    PromptStudioService.projectResolvedAssistantApiProjectsProjectIdPromptAssistantGet({
      projectId,
    }).then((r) => r as ResolvedAssistant),

  mergedTemplates: (projectId: string): Promise<PromptTemplate[]> =>
    PromptStudioService.projectMergedTemplatesApiProjectsProjectIdPromptTemplatesGet({
      projectId,
    }).then((r) => r as PromptTemplate[]),

  // --- streaming session ---
  createSession: (projectId: string): Promise<SessionCreated> =>
    PromptStudioService.createSessionApiProjectsProjectIdPromptAssistantSessionsPost({ projectId }),

  postMessage: (sessionId: string, payload: { content: string; editor_draft: string | null }) =>
    PromptStudioService.postMessageApiPromptAssistantSessionsSessionIdMessagesPost({
      sessionId,
      requestBody: payload,
    }),

  // Shared read reused by the config form's model picker.
  getModelCatalog: (): Promise<ModelCatalog> =>
    ModelCatalogService.getModelCatalogApiModelCatalogGet(),
}
