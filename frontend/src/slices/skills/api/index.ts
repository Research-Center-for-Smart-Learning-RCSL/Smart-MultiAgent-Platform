// Skills API.
//
// Wraps the generated SkillsService over the one instrumented axios singleton; methods
// return the bare body. Scoped methods dispatch on SkillScopeRef.kind to the per-scope
// generated method family (agent* / project* / org* / admin*); scope is never a request
// field, only a call-site choice, mirroring the four backend routers. Bindings, bundle
// job status, and metrics are scope-neutral single endpoints.
//
// The generated *Out models type the backend's closed enum fields (scope, scan_status,
// kind, status) as `string`; the resolved body is returned as-is, and the narrow unions
// in ../types are applied by consumers where they switch on those fields.

import { SkillsService } from '@shared/api-client'
import { asBinaryFormField } from '@shared/transport'

import type {
  BundleExportStatusOut,
  BundleImportStatusOut,
  BundleJobOut,
  SkillBindingOut,
  SkillCopyIn,
  SkillCreateIn,
  SkillFileCreateIn,
  SkillFileOut,
  SkillFilePatchIn,
  SkillOut,
  SkillPageOut,
  SkillPatchIn,
  SkillScopeCountsOut,
  SkillScopeRef,
} from '../types'

function dispatchScope<T>(
  scope: SkillScopeRef,
  handlers: {
    agent: (agentId: string) => T
    project: (projectId: string) => T
    org: (orgId: string) => T
    platform: () => T
  },
): T {
  switch (scope.kind) {
    case 'agent':
      return handlers.agent(scope.agentId)
    case 'project':
      return handlers.project(scope.projectId)
    case 'org':
      return handlers.org(scope.orgId)
    case 'platform':
      return handlers.platform()
  }
}

export interface ListParams {
  includeDeleted?: boolean
  limit?: number
  offset?: number
}

export const skillsApi = {
  // --- skill CRUD (scoped) ---
  list: (scope: SkillScopeRef, params: ListParams = {}): Promise<SkillPageOut> => {
    // Spread only the keys that are set: `exactOptionalPropertyTypes` forbids passing an
    // explicit `undefined` to the generated methods' optional query params.
    const q = {
      ...(params.includeDeleted !== undefined && { includeDeleted: params.includeDeleted }),
      ...(params.limit !== undefined && { limit: params.limit }),
      ...(params.offset !== undefined && { offset: params.offset }),
    }
    return dispatchScope(scope, {
      agent: (agentId) => SkillsService.agentListSkillsApiAgentsAgentIdSkillsGet({ agentId, ...q }),
      project: (projectId) =>
        SkillsService.projectListSkillsApiProjectsProjectIdSkillsGet({ projectId, ...q }),
      org: (orgId) => SkillsService.orgListSkillsApiOrgsOrgIdSkillsGet({ orgId, ...q }),
      platform: () => SkillsService.adminListSkillsApiAdminSkillsGet({ ...q }),
    })
  },

  get: (scope: SkillScopeRef, skillId: string): Promise<SkillOut> =>
    dispatchScope(scope, {
      agent: (agentId) => SkillsService.agentGetSkillApiAgentsAgentIdSkillsSkillIdGet({ agentId, skillId }),
      project: (projectId) =>
        SkillsService.projectGetSkillApiProjectsProjectIdSkillsSkillIdGet({ projectId, skillId }),
      org: (orgId) => SkillsService.orgGetSkillApiOrgsOrgIdSkillsSkillIdGet({ orgId, skillId }),
      platform: () => SkillsService.adminGetSkillApiAdminSkillsSkillIdGet({ skillId }),
    }),

  create: (scope: SkillScopeRef, body: SkillCreateIn): Promise<SkillOut> =>
    dispatchScope(scope, {
      agent: (agentId) =>
        SkillsService.agentCreateSkillApiAgentsAgentIdSkillsPost({ agentId, requestBody: body }),
      project: (projectId) =>
        SkillsService.projectCreateSkillApiProjectsProjectIdSkillsPost({ projectId, requestBody: body }),
      org: (orgId) => SkillsService.orgCreateSkillApiOrgsOrgIdSkillsPost({ orgId, requestBody: body }),
      platform: () => SkillsService.adminCreateSkillApiAdminSkillsPost({ requestBody: body }),
    }),

  patch: (
    scope: SkillScopeRef,
    skillId: string,
    version: number | null,
    body: SkillPatchIn,
  ): Promise<SkillOut> => {
    // null version -> no If-Match; the generated request core drops null headers.
    const ifMatch = version === null ? null : String(version)
    return dispatchScope(scope, {
      agent: (agentId) =>
        SkillsService.agentPatchSkillApiAgentsAgentIdSkillsSkillIdPatch({
          agentId,
          skillId,
          ifMatch,
          requestBody: body,
        }),
      project: (projectId) =>
        SkillsService.projectPatchSkillApiProjectsProjectIdSkillsSkillIdPatch({
          projectId,
          skillId,
          ifMatch,
          requestBody: body,
        }),
      org: (orgId) =>
        SkillsService.orgPatchSkillApiOrgsOrgIdSkillsSkillIdPatch({
          orgId,
          skillId,
          ifMatch,
          requestBody: body,
        }),
      platform: () =>
        SkillsService.adminPatchSkillApiAdminSkillsSkillIdPatch({ skillId, ifMatch, requestBody: body }),
    })
  },

  remove: (scope: SkillScopeRef, skillId: string, version: number | null): Promise<void> => {
    const ifMatch = version === null ? null : String(version)
    return dispatchScope(scope, {
      agent: (agentId) =>
        SkillsService.agentDeleteSkillApiAgentsAgentIdSkillsSkillIdDelete({ agentId, skillId, ifMatch }),
      project: (projectId) =>
        SkillsService.projectDeleteSkillApiProjectsProjectIdSkillsSkillIdDelete({
          projectId,
          skillId,
          ifMatch,
        }),
      org: (orgId) => SkillsService.orgDeleteSkillApiOrgsOrgIdSkillsSkillIdDelete({ orgId, skillId, ifMatch }),
      platform: () => SkillsService.adminDeleteSkillApiAdminSkillsSkillIdDelete({ skillId, ifMatch }),
    })
  },

  restore: (scope: SkillScopeRef, skillId: string): Promise<SkillOut> =>
    dispatchScope(scope, {
      agent: (agentId) =>
        SkillsService.agentRestoreSkillApiAgentsAgentIdSkillsSkillIdRestorePost({ agentId, skillId }),
      project: (projectId) =>
        SkillsService.projectRestoreSkillApiProjectsProjectIdSkillsSkillIdRestorePost({ projectId, skillId }),
      org: (orgId) => SkillsService.orgRestoreSkillApiOrgsOrgIdSkillsSkillIdRestorePost({ orgId, skillId }),
      platform: () => SkillsService.adminRestoreSkillApiAdminSkillsSkillIdRestorePost({ skillId }),
    }),

  copy: (scope: SkillScopeRef, skillId: string, body: SkillCopyIn): Promise<SkillOut> =>
    dispatchScope(scope, {
      agent: (agentId) =>
        SkillsService.agentCopySkillApiAgentsAgentIdSkillsSkillIdCopyPost({ agentId, skillId, requestBody: body }),
      project: (projectId) =>
        SkillsService.projectCopySkillApiProjectsProjectIdSkillsSkillIdCopyPost({
          projectId,
          skillId,
          requestBody: body,
        }),
      org: (orgId) =>
        SkillsService.orgCopySkillApiOrgsOrgIdSkillsSkillIdCopyPost({ orgId, skillId, requestBody: body }),
      platform: () => SkillsService.adminCopySkillApiAdminSkillsSkillIdCopyPost({ skillId, requestBody: body }),
    }),

  // --- bundled files (scoped) ---
  listFiles: (scope: SkillScopeRef, skillId: string): Promise<SkillFileOut[]> =>
    dispatchScope(scope, {
      agent: (agentId) => SkillsService.agentListFilesApiAgentsAgentIdSkillsSkillIdFilesGet({ agentId, skillId }),
      project: (projectId) =>
        SkillsService.projectListFilesApiProjectsProjectIdSkillsSkillIdFilesGet({ projectId, skillId }),
      org: (orgId) => SkillsService.orgListFilesApiOrgsOrgIdSkillsSkillIdFilesGet({ orgId, skillId }),
      platform: () => SkillsService.adminListFilesApiAdminSkillsSkillIdFilesGet({ skillId }),
    }),

  createFile: (scope: SkillScopeRef, skillId: string, body: SkillFileCreateIn): Promise<SkillFileOut> =>
    dispatchScope(scope, {
      agent: (agentId) =>
        SkillsService.agentCreateFileApiAgentsAgentIdSkillsSkillIdFilesPost({ agentId, skillId, requestBody: body }),
      project: (projectId) =>
        SkillsService.projectCreateFileApiProjectsProjectIdSkillsSkillIdFilesPost({
          projectId,
          skillId,
          requestBody: body,
        }),
      org: (orgId) =>
        SkillsService.orgCreateFileApiOrgsOrgIdSkillsSkillIdFilesPost({ orgId, skillId, requestBody: body }),
      platform: () => SkillsService.adminCreateFileApiAdminSkillsSkillIdFilesPost({ skillId, requestBody: body }),
    }),

  uploadFile: (scope: SkillScopeRef, skillId: string, path: string, file: File): Promise<SkillFileOut> => {
    const formData = { path, upload: asBinaryFormField(file) }
    return dispatchScope(scope, {
      agent: (agentId) =>
        SkillsService.agentUploadFileApiAgentsAgentIdSkillsSkillIdFilesUploadPost({ agentId, skillId, formData }),
      project: (projectId) =>
        SkillsService.projectUploadFileApiProjectsProjectIdSkillsSkillIdFilesUploadPost({
          projectId,
          skillId,
          formData,
        }),
      org: (orgId) =>
        SkillsService.orgUploadFileApiOrgsOrgIdSkillsSkillIdFilesUploadPost({ orgId, skillId, formData }),
      platform: () => SkillsService.adminUploadFileApiAdminSkillsSkillIdFilesUploadPost({ skillId, formData }),
    })
  },

  patchFile: (
    scope: SkillScopeRef,
    skillId: string,
    fileId: string,
    body: SkillFilePatchIn,
  ): Promise<SkillFileOut> =>
    dispatchScope(scope, {
      agent: (agentId) =>
        SkillsService.agentPatchFileApiAgentsAgentIdSkillsSkillIdFilesFileIdPatch({
          agentId,
          skillId,
          fileId,
          requestBody: body,
        }),
      project: (projectId) =>
        SkillsService.projectPatchFileApiProjectsProjectIdSkillsSkillIdFilesFileIdPatch({
          projectId,
          skillId,
          fileId,
          requestBody: body,
        }),
      org: (orgId) =>
        SkillsService.orgPatchFileApiOrgsOrgIdSkillsSkillIdFilesFileIdPatch({
          orgId,
          skillId,
          fileId,
          requestBody: body,
        }),
      platform: () =>
        SkillsService.adminPatchFileApiAdminSkillsSkillIdFilesFileIdPatch({ skillId, fileId, requestBody: body }),
    }),

  deleteFile: (scope: SkillScopeRef, skillId: string, fileId: string): Promise<void> =>
    dispatchScope(scope, {
      agent: (agentId) =>
        SkillsService.agentDeleteFileApiAgentsAgentIdSkillsSkillIdFilesFileIdDelete({ agentId, skillId, fileId }),
      project: (projectId) =>
        SkillsService.projectDeleteFileApiProjectsProjectIdSkillsSkillIdFilesFileIdDelete({
          projectId,
          skillId,
          fileId,
        }),
      org: (orgId) =>
        SkillsService.orgDeleteFileApiOrgsOrgIdSkillsSkillIdFilesFileIdDelete({ orgId, skillId, fileId }),
      platform: () => SkillsService.adminDeleteFileApiAdminSkillsSkillIdFilesFileIdDelete({ skillId, fileId }),
    }),

  // --- bundle transport (scoped enqueue + scope-neutral status) ---
  importBundle: (scope: SkillScopeRef, file: File): Promise<BundleJobOut> => {
    const formData = { upload: asBinaryFormField(file) }
    return dispatchScope(scope, {
      agent: (agentId) => SkillsService.agentImportBundleApiAgentsAgentIdSkillsImportPost({ agentId, formData }),
      project: (projectId) =>
        SkillsService.projectImportBundleApiProjectsProjectIdSkillsImportPost({ projectId, formData }),
      org: (orgId) => SkillsService.orgImportBundleApiOrgsOrgIdSkillsImportPost({ orgId, formData }),
      platform: () => SkillsService.adminImportBundleApiAdminSkillsImportPost({ formData }),
    })
  },

  exportBundle: (scope: SkillScopeRef, skillId: string): Promise<BundleJobOut> =>
    dispatchScope(scope, {
      agent: (agentId) =>
        SkillsService.agentExportBundleApiAgentsAgentIdSkillsSkillIdExportGet({ agentId, skillId }),
      project: (projectId) =>
        SkillsService.projectExportBundleApiProjectsProjectIdSkillsSkillIdExportGet({ projectId, skillId }),
      org: (orgId) => SkillsService.orgExportBundleApiOrgsOrgIdSkillsSkillIdExportGet({ orgId, skillId }),
      platform: () => SkillsService.adminExportBundleApiAdminSkillsSkillIdExportGet({ skillId }),
    }),

  importStatus: (taskId: string): Promise<BundleImportStatusOut> =>
    SkillsService.getImportStatusApiSkillsImportsTaskIdGet({ taskId }),

  exportStatus: (taskId: string): Promise<BundleExportStatusOut> =>
    SkillsService.getExportStatusApiSkillsExportsTaskIdGet({ taskId }),

  // --- bindings (agent-scoped, no scope path) ---
  listBindings: (agentId: string): Promise<SkillBindingOut[]> =>
    SkillsService.listBindingsApiAgentsAgentIdSkillBindingsGet({ agentId }),

  bind: (agentId: string, skillId: string): Promise<void> =>
    SkillsService.bindSkillApiAgentsAgentIdSkillBindingsSkillIdPut({ agentId, skillId }),

  unbind: (agentId: string, skillId: string): Promise<void> =>
    SkillsService.unbindSkillApiAgentsAgentIdSkillBindingsSkillIdDelete({ agentId, skillId }),

  // --- admin metrics (platform, scope-neutral) ---
  metrics: (): Promise<SkillScopeCountsOut> => SkillsService.adminSkillMetricsApiAdminSkillsMetricsGet(),
}
