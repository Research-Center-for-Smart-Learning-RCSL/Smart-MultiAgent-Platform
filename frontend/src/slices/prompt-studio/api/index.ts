// Prompt-studio API.
//
// Wraps the generated PromptStudioService over the one instrumented axios singleton. The
// methods return the bare body, so consumers no longer read `.data`.
//
// Scoped methods dispatch on ConfigScopeRef.kind to the per-scope generated method family
// (user -> me*, org -> org*, platform -> admin*), replacing the old configBase/templateBase
// URL builders. The generated *Out models type the backend enum fields (scan_status, scope,
// source_scope) as `string`; the hand-rolled types narrow them to unions, so the resolved
// body is cast back at the boundary — the same unchecked assertion the previous
// `http.get<T>()` calls made, safe because these are backend-closed enums.

import type { AssistantSessionOut } from '@shared/api-client'
import { PromptStudioService } from '@shared/api-client'
import { asBinaryFormField } from '@shared/transport'

import type {
  AssistantConfig,
  AssistantConfigPresetInput,
  AssistantConfigPutInput,
  AssistantFile,
  ConfigEnvelope,
  ConfigScopeRef,
  PromptTemplate,
  ResolvedAssistant,
  SessionCreated,
  TemplateCreateInput,
  TemplatePatchInput,
  TemplateScopeRef,
} from '../types'

// Route a scoped config call to the per-scope generated method. Platform
// scope has no singleton config endpoint any more (see the preset* methods
// below) -- only user/org remain.
function dispatchConfigScope<T>(scope: ConfigScopeRef, handlers: { user: () => T; org: (orgId: string) => T }): T {
  return scope.kind === 'user' ? handlers.user() : handlers.org(scope.orgId)
}

// Route a scoped template call to the per-scope generated method. `platform`
// targets the admin* endpoints (platform-wide templates, unaffected by the
// persona/preset work).
function dispatchTemplateScope<T>(
  scope: TemplateScopeRef,
  handlers: { user: () => T; org: (orgId: string) => T; platform: () => T },
): T {
  if (scope.kind === 'user') return handlers.user()
  if (scope.kind === 'org') return handlers.org(scope.orgId)
  return handlers.platform()
}

export const promptStudioApi = {
  // --- config (scoped: user/org only -- platform is presets, below) ---
  getConfig: (scope: ConfigScopeRef): Promise<ConfigEnvelope> =>
    dispatchConfigScope(scope, {
      user: () => PromptStudioService.meGetConfigApiMePromptAssistantConfigGet(),
      org: (orgId) => PromptStudioService.orgGetConfigApiOrgsOrgIdPromptAssistantConfigGet({ orgId }),
    }).then((r) => r as ConfigEnvelope),

  putConfig: (
    scope: ConfigScopeRef,
    version: number | null,
    payload: AssistantConfigPutInput,
  ): Promise<AssistantConfig> => {
    // null version -> no If-Match; the generated request core drops null headers.
    const ifMatch = version === null ? null : String(version)
    return dispatchConfigScope(scope, {
      user: () =>
        PromptStudioService.mePutConfigApiMePromptAssistantConfigPut({ requestBody: payload, ifMatch }),
      org: (orgId) =>
        PromptStudioService.orgPutConfigApiOrgsOrgIdPromptAssistantConfigPut({
          orgId,
          requestBody: payload,
          ifMatch,
        }),
    }).then((r) => r as AssistantConfig)
  },

  uploadFile: (scope: ConfigScopeRef, file: File): Promise<AssistantFile> => {
    const formData = { file: asBinaryFormField(file) }
    return dispatchConfigScope(scope, {
      user: () => PromptStudioService.meUploadFileApiMePromptAssistantConfigFilesPost({ formData }),
      org: (orgId) =>
        PromptStudioService.orgUploadFileApiOrgsOrgIdPromptAssistantConfigFilesPost({ orgId, formData }),
    }).then((r) => r as AssistantFile)
  },

  deleteFile: (scope: ConfigScopeRef, fileId: string) =>
    dispatchConfigScope(scope, {
      user: () => PromptStudioService.meDeleteFileApiMePromptAssistantConfigFilesFileIdDelete({ fileId }),
      org: (orgId) =>
        PromptStudioService.orgDeleteFileApiOrgsOrgIdPromptAssistantConfigFilesFileIdDelete({
          orgId,
          fileId,
        }),
    }),

  // --- platform presets (id-addressed, not scope-addressed; R29.16) ---
  listPresets: (): Promise<AssistantConfig[]> =>
    PromptStudioService.adminListPresetsApiAdminPromptAssistantPresetsGet().then((r) => r as AssistantConfig[]),

  createPreset: (payload: AssistantConfigPresetInput): Promise<AssistantConfig> =>
    PromptStudioService.adminCreatePresetApiAdminPromptAssistantPresetsPost({ requestBody: payload }).then(
      (r) => r as AssistantConfig,
    ),

  updatePreset: (
    presetId: string,
    version: number,
    payload: AssistantConfigPresetInput,
  ): Promise<AssistantConfig> =>
    PromptStudioService.adminUpdatePresetApiAdminPromptAssistantPresetsConfigIdPut({
      configId: presetId,
      ifMatch: String(version),
      requestBody: payload,
    }).then((r) => r as AssistantConfig),

  deletePreset: (presetId: string) =>
    PromptStudioService.adminDeletePresetApiAdminPromptAssistantPresetsConfigIdDelete({ configId: presetId }),

  uploadPresetFile: (presetId: string, file: File): Promise<AssistantFile> =>
    PromptStudioService.adminUploadPresetFileApiAdminPromptAssistantPresetsConfigIdFilesPost({
      configId: presetId,
      formData: { file: asBinaryFormField(file) },
    }).then((r) => r as AssistantFile),

  deletePresetFile: (presetId: string, fileId: string) =>
    PromptStudioService.adminDeletePresetFileApiAdminPromptAssistantPresetsConfigIdFilesFileIdDelete({
      configId: presetId,
      fileId,
    }),

  // --- templates (scoped CRUD, including platform) ---
  listTemplates: (scope: TemplateScopeRef): Promise<PromptTemplate[]> =>
    dispatchTemplateScope(scope, {
      user: () => PromptStudioService.meListTemplatesApiMePromptTemplatesGet(),
      org: (orgId) => PromptStudioService.orgListTemplatesApiOrgsOrgIdPromptTemplatesGet({ orgId }),
      platform: () => PromptStudioService.adminListTemplatesApiAdminPromptTemplatesGet(),
    }).then((r) => r as PromptTemplate[]),

  createTemplate: (scope: TemplateScopeRef, payload: TemplateCreateInput): Promise<PromptTemplate> =>
    dispatchTemplateScope(scope, {
      user: () => PromptStudioService.meCreateTemplateApiMePromptTemplatesPost({ requestBody: payload }),
      org: (orgId) =>
        PromptStudioService.orgCreateTemplateApiOrgsOrgIdPromptTemplatesPost({ orgId, requestBody: payload }),
      platform: () =>
        PromptStudioService.adminCreateTemplateApiAdminPromptTemplatesPost({ requestBody: payload }),
    }).then((r) => r as PromptTemplate),

  patchTemplate: (
    scope: TemplateScopeRef,
    id: string,
    version: number,
    payload: TemplatePatchInput,
  ): Promise<PromptTemplate> => {
    const ifMatch = String(version)
    return dispatchTemplateScope(scope, {
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

  deleteTemplate: (scope: TemplateScopeRef, id: string) =>
    dispatchTemplateScope(scope, {
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

  getSession: (sessionId: string): Promise<AssistantSessionOut> =>
    PromptStudioService.getSessionApiPromptAssistantSessionsSessionIdGet({ sessionId }),
}
