/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Body_admin_import_bundle_api_admin_skills_import_post } from '../models/Body_admin_import_bundle_api_admin_skills_import_post';
import type { Body_admin_upload_file_api_admin_skills__skill_id__files_upload_post } from '../models/Body_admin_upload_file_api_admin_skills__skill_id__files_upload_post';
import type { Body_agent_import_bundle_api_agents__agent_id__skills_import_post } from '../models/Body_agent_import_bundle_api_agents__agent_id__skills_import_post';
import type { Body_agent_upload_file_api_agents__agent_id__skills__skill_id__files_upload_post } from '../models/Body_agent_upload_file_api_agents__agent_id__skills__skill_id__files_upload_post';
import type { Body_org_import_bundle_api_orgs__org_id__skills_import_post } from '../models/Body_org_import_bundle_api_orgs__org_id__skills_import_post';
import type { Body_org_upload_file_api_orgs__org_id__skills__skill_id__files_upload_post } from '../models/Body_org_upload_file_api_orgs__org_id__skills__skill_id__files_upload_post';
import type { Body_project_import_bundle_api_projects__project_id__skills_import_post } from '../models/Body_project_import_bundle_api_projects__project_id__skills_import_post';
import type { Body_project_upload_file_api_projects__project_id__skills__skill_id__files_upload_post } from '../models/Body_project_upload_file_api_projects__project_id__skills__skill_id__files_upload_post';
import type { BundleExportStatusOut } from '../models/BundleExportStatusOut';
import type { BundleImportStatusOut } from '../models/BundleImportStatusOut';
import type { BundleJobOut } from '../models/BundleJobOut';
import type { SkillBindingOut } from '../models/SkillBindingOut';
import type { SkillCopyIn } from '../models/SkillCopyIn';
import type { SkillCreateIn } from '../models/SkillCreateIn';
import type { SkillFileCreateIn } from '../models/SkillFileCreateIn';
import type { SkillFileOut } from '../models/SkillFileOut';
import type { SkillFilePatchIn } from '../models/SkillFilePatchIn';
import type { SkillOut } from '../models/SkillOut';
import type { SkillPageOut } from '../models/SkillPageOut';
import type { SkillPatchIn } from '../models/SkillPatchIn';
import type { SkillScopeCountsOut } from '../models/SkillScopeCountsOut';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class SkillsService {
    /**
     * Admin List Skills
     * @returns SkillPageOut Successful Response
     * @throws ApiError
     */
    public static adminListSkillsApiAdminSkillsGet({
        includeDeleted = false,
        limit = 100,
        offset,
    }: {
        includeDeleted?: boolean,
        /**
         * Max items to return
         */
        limit?: number,
        /**
         * Number of items to skip
         */
        offset?: number,
    }): CancelablePromise<SkillPageOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/admin/skills',
            query: {
                'include_deleted': includeDeleted,
                'limit': limit,
                'offset': offset,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Admin Create Skill
     * @returns SkillOut Successful Response
     * @throws ApiError
     */
    public static adminCreateSkillApiAdminSkillsPost({
        requestBody,
    }: {
        requestBody: SkillCreateIn,
    }): CancelablePromise<SkillOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/admin/skills',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Admin Import Bundle
     * @returns BundleJobOut Successful Response
     * @throws ApiError
     */
    public static adminImportBundleApiAdminSkillsImportPost({
        formData,
    }: {
        formData: Body_admin_import_bundle_api_admin_skills_import_post,
    }): CancelablePromise<BundleJobOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/admin/skills/import',
            formData: formData,
            mediaType: 'multipart/form-data',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Admin Skill Metrics
     * [R31.11] / AC-15 — the ratio of agent-private to shared skills.
     *
     * Not decoration: §5's premise is that skills are shared at project scope and above.
     * If most end up agent-scoped, Skills has degraded into the §9.2 prompt-strategy
     * feature it replaced, and this endpoint is what makes that visible rather than a
     * matter of opinion at the six-month review.
     *
     * Declared **before** `/{skill_id}`: FastAPI matches in declaration order, and the
     * UUID-typed path below would otherwise claim "metrics" and 422 it.
     * @returns SkillScopeCountsOut Successful Response
     * @throws ApiError
     */
    public static adminSkillMetricsApiAdminSkillsMetricsGet(): CancelablePromise<SkillScopeCountsOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/admin/skills/metrics',
        });
    }
    /**
     * Admin Delete Skill
     * @returns void
     * @throws ApiError
     */
    public static adminDeleteSkillApiAdminSkillsSkillIdDelete({
        skillId,
        ifMatch,
    }: {
        skillId: string,
        ifMatch?: (string | null),
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/admin/skills/{skill_id}',
            path: {
                'skill_id': skillId,
            },
            headers: {
                'If-Match': ifMatch,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Admin Get Skill
     * @returns SkillOut Successful Response
     * @throws ApiError
     */
    public static adminGetSkillApiAdminSkillsSkillIdGet({
        skillId,
    }: {
        skillId: string,
    }): CancelablePromise<SkillOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/admin/skills/{skill_id}',
            path: {
                'skill_id': skillId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Admin Patch Skill
     * @returns SkillOut Successful Response
     * @throws ApiError
     */
    public static adminPatchSkillApiAdminSkillsSkillIdPatch({
        skillId,
        requestBody,
        ifMatch,
    }: {
        skillId: string,
        requestBody: SkillPatchIn,
        ifMatch?: (string | null),
    }): CancelablePromise<SkillOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/admin/skills/{skill_id}',
            path: {
                'skill_id': skillId,
            },
            headers: {
                'If-Match': ifMatch,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Admin Copy Skill
     * @returns SkillOut Successful Response
     * @throws ApiError
     */
    public static adminCopySkillApiAdminSkillsSkillIdCopyPost({
        skillId,
        requestBody,
    }: {
        skillId: string,
        requestBody: SkillCopyIn,
    }): CancelablePromise<SkillOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/admin/skills/{skill_id}/copy',
            path: {
                'skill_id': skillId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Admin Export Bundle
     * @returns BundleJobOut Successful Response
     * @throws ApiError
     */
    public static adminExportBundleApiAdminSkillsSkillIdExportGet({
        skillId,
    }: {
        skillId: string,
    }): CancelablePromise<BundleJobOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/admin/skills/{skill_id}/export',
            path: {
                'skill_id': skillId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Admin List Files
     * @returns SkillFileOut Successful Response
     * @throws ApiError
     */
    public static adminListFilesApiAdminSkillsSkillIdFilesGet({
        skillId,
    }: {
        skillId: string,
    }): CancelablePromise<Array<SkillFileOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/admin/skills/{skill_id}/files',
            path: {
                'skill_id': skillId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Admin Create File
     * @returns SkillFileOut Successful Response
     * @throws ApiError
     */
    public static adminCreateFileApiAdminSkillsSkillIdFilesPost({
        skillId,
        requestBody,
    }: {
        skillId: string,
        requestBody: SkillFileCreateIn,
    }): CancelablePromise<SkillFileOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/admin/skills/{skill_id}/files',
            path: {
                'skill_id': skillId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Admin Upload File
     * @returns SkillFileOut Successful Response
     * @throws ApiError
     */
    public static adminUploadFileApiAdminSkillsSkillIdFilesUploadPost({
        skillId,
        formData,
    }: {
        skillId: string,
        formData: Body_admin_upload_file_api_admin_skills__skill_id__files_upload_post,
    }): CancelablePromise<SkillFileOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/admin/skills/{skill_id}/files/upload',
            path: {
                'skill_id': skillId,
            },
            formData: formData,
            mediaType: 'multipart/form-data',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Admin Delete File
     * @returns void
     * @throws ApiError
     */
    public static adminDeleteFileApiAdminSkillsSkillIdFilesFileIdDelete({
        skillId,
        fileId,
    }: {
        skillId: string,
        fileId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/admin/skills/{skill_id}/files/{file_id}',
            path: {
                'skill_id': skillId,
                'file_id': fileId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Admin Patch File
     * @returns SkillFileOut Successful Response
     * @throws ApiError
     */
    public static adminPatchFileApiAdminSkillsSkillIdFilesFileIdPatch({
        skillId,
        fileId,
        requestBody,
    }: {
        skillId: string,
        fileId: string,
        requestBody: SkillFilePatchIn,
    }): CancelablePromise<SkillFileOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/admin/skills/{skill_id}/files/{file_id}',
            path: {
                'skill_id': skillId,
                'file_id': fileId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Admin Restore Skill
     * @returns SkillOut Successful Response
     * @throws ApiError
     */
    public static adminRestoreSkillApiAdminSkillsSkillIdRestorePost({
        skillId,
    }: {
        skillId: string,
    }): CancelablePromise<SkillOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/admin/skills/{skill_id}/restore',
            path: {
                'skill_id': skillId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * List Bindings
     * @returns SkillBindingOut Successful Response
     * @throws ApiError
     */
    public static listBindingsApiAgentsAgentIdSkillBindingsGet({
        agentId,
    }: {
        agentId: string,
    }): CancelablePromise<Array<SkillBindingOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/agents/{agent_id}/skill-bindings',
            path: {
                'agent_id': agentId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Unbind Skill
     * @returns void
     * @throws ApiError
     */
    public static unbindSkillApiAgentsAgentIdSkillBindingsSkillIdDelete({
        agentId,
        skillId,
    }: {
        agentId: string,
        skillId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/agents/{agent_id}/skill-bindings/{skill_id}',
            path: {
                'agent_id': agentId,
                'skill_id': skillId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Bind Skill
     * Bind a skill to an agent.
     *
     * The path carries no scope, so the capability check below authorizes the *agent* only.
     * `resolve_bindable` inside `bind` is what proves the skill's scope contains it — taking
     * `skill_id` on trust here is the SEC-H1 IDOR with instructions in place of chunks.
     * @returns void
     * @throws ApiError
     */
    public static bindSkillApiAgentsAgentIdSkillBindingsSkillIdPut({
        agentId,
        skillId,
    }: {
        agentId: string,
        skillId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/agents/{agent_id}/skill-bindings/{skill_id}',
            path: {
                'agent_id': agentId,
                'skill_id': skillId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Agent List Skills
     * @returns SkillPageOut Successful Response
     * @throws ApiError
     */
    public static agentListSkillsApiAgentsAgentIdSkillsGet({
        agentId,
        includeDeleted = false,
        limit = 100,
        offset,
    }: {
        agentId: string,
        includeDeleted?: boolean,
        /**
         * Max items to return
         */
        limit?: number,
        /**
         * Number of items to skip
         */
        offset?: number,
    }): CancelablePromise<SkillPageOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/agents/{agent_id}/skills',
            path: {
                'agent_id': agentId,
            },
            query: {
                'include_deleted': includeDeleted,
                'limit': limit,
                'offset': offset,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Agent Create Skill
     * @returns SkillOut Successful Response
     * @throws ApiError
     */
    public static agentCreateSkillApiAgentsAgentIdSkillsPost({
        agentId,
        requestBody,
    }: {
        agentId: string,
        requestBody: SkillCreateIn,
    }): CancelablePromise<SkillOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/agents/{agent_id}/skills',
            path: {
                'agent_id': agentId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Agent Import Bundle
     * @returns BundleJobOut Successful Response
     * @throws ApiError
     */
    public static agentImportBundleApiAgentsAgentIdSkillsImportPost({
        agentId,
        formData,
    }: {
        agentId: string,
        formData: Body_agent_import_bundle_api_agents__agent_id__skills_import_post,
    }): CancelablePromise<BundleJobOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/agents/{agent_id}/skills/import',
            path: {
                'agent_id': agentId,
            },
            formData: formData,
            mediaType: 'multipart/form-data',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Agent Delete Skill
     * @returns void
     * @throws ApiError
     */
    public static agentDeleteSkillApiAgentsAgentIdSkillsSkillIdDelete({
        agentId,
        skillId,
        ifMatch,
    }: {
        agentId: string,
        skillId: string,
        ifMatch?: (string | null),
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/agents/{agent_id}/skills/{skill_id}',
            path: {
                'agent_id': agentId,
                'skill_id': skillId,
            },
            headers: {
                'If-Match': ifMatch,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Agent Get Skill
     * @returns SkillOut Successful Response
     * @throws ApiError
     */
    public static agentGetSkillApiAgentsAgentIdSkillsSkillIdGet({
        agentId,
        skillId,
    }: {
        agentId: string,
        skillId: string,
    }): CancelablePromise<SkillOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/agents/{agent_id}/skills/{skill_id}',
            path: {
                'agent_id': agentId,
                'skill_id': skillId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Agent Patch Skill
     * @returns SkillOut Successful Response
     * @throws ApiError
     */
    public static agentPatchSkillApiAgentsAgentIdSkillsSkillIdPatch({
        agentId,
        skillId,
        requestBody,
        ifMatch,
    }: {
        agentId: string,
        skillId: string,
        requestBody: SkillPatchIn,
        ifMatch?: (string | null),
    }): CancelablePromise<SkillOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/agents/{agent_id}/skills/{skill_id}',
            path: {
                'agent_id': agentId,
                'skill_id': skillId,
            },
            headers: {
                'If-Match': ifMatch,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Agent Copy Skill
     * @returns SkillOut Successful Response
     * @throws ApiError
     */
    public static agentCopySkillApiAgentsAgentIdSkillsSkillIdCopyPost({
        agentId,
        skillId,
        requestBody,
    }: {
        agentId: string,
        skillId: string,
        requestBody: SkillCopyIn,
    }): CancelablePromise<SkillOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/agents/{agent_id}/skills/{skill_id}/copy',
            path: {
                'agent_id': agentId,
                'skill_id': skillId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Agent Export Bundle
     * @returns BundleJobOut Successful Response
     * @throws ApiError
     */
    public static agentExportBundleApiAgentsAgentIdSkillsSkillIdExportGet({
        agentId,
        skillId,
    }: {
        agentId: string,
        skillId: string,
    }): CancelablePromise<BundleJobOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/agents/{agent_id}/skills/{skill_id}/export',
            path: {
                'agent_id': agentId,
                'skill_id': skillId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Agent List Files
     * @returns SkillFileOut Successful Response
     * @throws ApiError
     */
    public static agentListFilesApiAgentsAgentIdSkillsSkillIdFilesGet({
        agentId,
        skillId,
    }: {
        agentId: string,
        skillId: string,
    }): CancelablePromise<Array<SkillFileOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/agents/{agent_id}/skills/{skill_id}/files',
            path: {
                'agent_id': agentId,
                'skill_id': skillId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Agent Create File
     * @returns SkillFileOut Successful Response
     * @throws ApiError
     */
    public static agentCreateFileApiAgentsAgentIdSkillsSkillIdFilesPost({
        agentId,
        skillId,
        requestBody,
    }: {
        agentId: string,
        skillId: string,
        requestBody: SkillFileCreateIn,
    }): CancelablePromise<SkillFileOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/agents/{agent_id}/skills/{skill_id}/files',
            path: {
                'agent_id': agentId,
                'skill_id': skillId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Agent Upload File
     * @returns SkillFileOut Successful Response
     * @throws ApiError
     */
    public static agentUploadFileApiAgentsAgentIdSkillsSkillIdFilesUploadPost({
        agentId,
        skillId,
        formData,
    }: {
        agentId: string,
        skillId: string,
        formData: Body_agent_upload_file_api_agents__agent_id__skills__skill_id__files_upload_post,
    }): CancelablePromise<SkillFileOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/agents/{agent_id}/skills/{skill_id}/files/upload',
            path: {
                'agent_id': agentId,
                'skill_id': skillId,
            },
            formData: formData,
            mediaType: 'multipart/form-data',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Agent Delete File
     * @returns void
     * @throws ApiError
     */
    public static agentDeleteFileApiAgentsAgentIdSkillsSkillIdFilesFileIdDelete({
        agentId,
        skillId,
        fileId,
    }: {
        agentId: string,
        skillId: string,
        fileId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/agents/{agent_id}/skills/{skill_id}/files/{file_id}',
            path: {
                'agent_id': agentId,
                'skill_id': skillId,
                'file_id': fileId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Agent Patch File
     * @returns SkillFileOut Successful Response
     * @throws ApiError
     */
    public static agentPatchFileApiAgentsAgentIdSkillsSkillIdFilesFileIdPatch({
        agentId,
        skillId,
        fileId,
        requestBody,
    }: {
        agentId: string,
        skillId: string,
        fileId: string,
        requestBody: SkillFilePatchIn,
    }): CancelablePromise<SkillFileOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/agents/{agent_id}/skills/{skill_id}/files/{file_id}',
            path: {
                'agent_id': agentId,
                'skill_id': skillId,
                'file_id': fileId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Agent Restore Skill
     * @returns SkillOut Successful Response
     * @throws ApiError
     */
    public static agentRestoreSkillApiAgentsAgentIdSkillsSkillIdRestorePost({
        agentId,
        skillId,
    }: {
        agentId: string,
        skillId: string,
    }): CancelablePromise<SkillOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/agents/{agent_id}/skills/{skill_id}/restore',
            path: {
                'agent_id': agentId,
                'skill_id': skillId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Org List Skills
     * @returns SkillPageOut Successful Response
     * @throws ApiError
     */
    public static orgListSkillsApiOrgsOrgIdSkillsGet({
        orgId,
        includeDeleted = false,
        limit = 100,
        offset,
    }: {
        orgId: string,
        includeDeleted?: boolean,
        /**
         * Max items to return
         */
        limit?: number,
        /**
         * Number of items to skip
         */
        offset?: number,
    }): CancelablePromise<SkillPageOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/orgs/{org_id}/skills',
            path: {
                'org_id': orgId,
            },
            query: {
                'include_deleted': includeDeleted,
                'limit': limit,
                'offset': offset,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Org Create Skill
     * @returns SkillOut Successful Response
     * @throws ApiError
     */
    public static orgCreateSkillApiOrgsOrgIdSkillsPost({
        orgId,
        requestBody,
    }: {
        orgId: string,
        requestBody: SkillCreateIn,
    }): CancelablePromise<SkillOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/orgs/{org_id}/skills',
            path: {
                'org_id': orgId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Org Import Bundle
     * @returns BundleJobOut Successful Response
     * @throws ApiError
     */
    public static orgImportBundleApiOrgsOrgIdSkillsImportPost({
        orgId,
        formData,
    }: {
        orgId: string,
        formData: Body_org_import_bundle_api_orgs__org_id__skills_import_post,
    }): CancelablePromise<BundleJobOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/orgs/{org_id}/skills/import',
            path: {
                'org_id': orgId,
            },
            formData: formData,
            mediaType: 'multipart/form-data',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Org Delete Skill
     * @returns void
     * @throws ApiError
     */
    public static orgDeleteSkillApiOrgsOrgIdSkillsSkillIdDelete({
        orgId,
        skillId,
        ifMatch,
    }: {
        orgId: string,
        skillId: string,
        ifMatch?: (string | null),
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/orgs/{org_id}/skills/{skill_id}',
            path: {
                'org_id': orgId,
                'skill_id': skillId,
            },
            headers: {
                'If-Match': ifMatch,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Org Get Skill
     * @returns SkillOut Successful Response
     * @throws ApiError
     */
    public static orgGetSkillApiOrgsOrgIdSkillsSkillIdGet({
        orgId,
        skillId,
    }: {
        orgId: string,
        skillId: string,
    }): CancelablePromise<SkillOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/orgs/{org_id}/skills/{skill_id}',
            path: {
                'org_id': orgId,
                'skill_id': skillId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Org Patch Skill
     * @returns SkillOut Successful Response
     * @throws ApiError
     */
    public static orgPatchSkillApiOrgsOrgIdSkillsSkillIdPatch({
        orgId,
        skillId,
        requestBody,
        ifMatch,
    }: {
        orgId: string,
        skillId: string,
        requestBody: SkillPatchIn,
        ifMatch?: (string | null),
    }): CancelablePromise<SkillOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/orgs/{org_id}/skills/{skill_id}',
            path: {
                'org_id': orgId,
                'skill_id': skillId,
            },
            headers: {
                'If-Match': ifMatch,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Org Copy Skill
     * @returns SkillOut Successful Response
     * @throws ApiError
     */
    public static orgCopySkillApiOrgsOrgIdSkillsSkillIdCopyPost({
        orgId,
        skillId,
        requestBody,
    }: {
        orgId: string,
        skillId: string,
        requestBody: SkillCopyIn,
    }): CancelablePromise<SkillOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/orgs/{org_id}/skills/{skill_id}/copy',
            path: {
                'org_id': orgId,
                'skill_id': skillId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Org Export Bundle
     * @returns BundleJobOut Successful Response
     * @throws ApiError
     */
    public static orgExportBundleApiOrgsOrgIdSkillsSkillIdExportGet({
        orgId,
        skillId,
    }: {
        orgId: string,
        skillId: string,
    }): CancelablePromise<BundleJobOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/orgs/{org_id}/skills/{skill_id}/export',
            path: {
                'org_id': orgId,
                'skill_id': skillId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Org List Files
     * @returns SkillFileOut Successful Response
     * @throws ApiError
     */
    public static orgListFilesApiOrgsOrgIdSkillsSkillIdFilesGet({
        orgId,
        skillId,
    }: {
        orgId: string,
        skillId: string,
    }): CancelablePromise<Array<SkillFileOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/orgs/{org_id}/skills/{skill_id}/files',
            path: {
                'org_id': orgId,
                'skill_id': skillId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Org Create File
     * @returns SkillFileOut Successful Response
     * @throws ApiError
     */
    public static orgCreateFileApiOrgsOrgIdSkillsSkillIdFilesPost({
        orgId,
        skillId,
        requestBody,
    }: {
        orgId: string,
        skillId: string,
        requestBody: SkillFileCreateIn,
    }): CancelablePromise<SkillFileOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/orgs/{org_id}/skills/{skill_id}/files',
            path: {
                'org_id': orgId,
                'skill_id': skillId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Org Upload File
     * @returns SkillFileOut Successful Response
     * @throws ApiError
     */
    public static orgUploadFileApiOrgsOrgIdSkillsSkillIdFilesUploadPost({
        orgId,
        skillId,
        formData,
    }: {
        orgId: string,
        skillId: string,
        formData: Body_org_upload_file_api_orgs__org_id__skills__skill_id__files_upload_post,
    }): CancelablePromise<SkillFileOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/orgs/{org_id}/skills/{skill_id}/files/upload',
            path: {
                'org_id': orgId,
                'skill_id': skillId,
            },
            formData: formData,
            mediaType: 'multipart/form-data',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Org Delete File
     * @returns void
     * @throws ApiError
     */
    public static orgDeleteFileApiOrgsOrgIdSkillsSkillIdFilesFileIdDelete({
        orgId,
        skillId,
        fileId,
    }: {
        orgId: string,
        skillId: string,
        fileId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/orgs/{org_id}/skills/{skill_id}/files/{file_id}',
            path: {
                'org_id': orgId,
                'skill_id': skillId,
                'file_id': fileId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Org Patch File
     * @returns SkillFileOut Successful Response
     * @throws ApiError
     */
    public static orgPatchFileApiOrgsOrgIdSkillsSkillIdFilesFileIdPatch({
        orgId,
        skillId,
        fileId,
        requestBody,
    }: {
        orgId: string,
        skillId: string,
        fileId: string,
        requestBody: SkillFilePatchIn,
    }): CancelablePromise<SkillFileOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/orgs/{org_id}/skills/{skill_id}/files/{file_id}',
            path: {
                'org_id': orgId,
                'skill_id': skillId,
                'file_id': fileId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Org Restore Skill
     * @returns SkillOut Successful Response
     * @throws ApiError
     */
    public static orgRestoreSkillApiOrgsOrgIdSkillsSkillIdRestorePost({
        orgId,
        skillId,
    }: {
        orgId: string,
        skillId: string,
    }): CancelablePromise<SkillOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/orgs/{org_id}/skills/{skill_id}/restore',
            path: {
                'org_id': orgId,
                'skill_id': skillId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Project List Skills
     * @returns SkillPageOut Successful Response
     * @throws ApiError
     */
    public static projectListSkillsApiProjectsProjectIdSkillsGet({
        projectId,
        includeDeleted = false,
        limit = 100,
        offset,
    }: {
        projectId: string,
        includeDeleted?: boolean,
        /**
         * Max items to return
         */
        limit?: number,
        /**
         * Number of items to skip
         */
        offset?: number,
    }): CancelablePromise<SkillPageOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/projects/{project_id}/skills',
            path: {
                'project_id': projectId,
            },
            query: {
                'include_deleted': includeDeleted,
                'limit': limit,
                'offset': offset,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Project Create Skill
     * @returns SkillOut Successful Response
     * @throws ApiError
     */
    public static projectCreateSkillApiProjectsProjectIdSkillsPost({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: SkillCreateIn,
    }): CancelablePromise<SkillOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/projects/{project_id}/skills',
            path: {
                'project_id': projectId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Project Import Bundle
     * @returns BundleJobOut Successful Response
     * @throws ApiError
     */
    public static projectImportBundleApiProjectsProjectIdSkillsImportPost({
        projectId,
        formData,
    }: {
        projectId: string,
        formData: Body_project_import_bundle_api_projects__project_id__skills_import_post,
    }): CancelablePromise<BundleJobOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/projects/{project_id}/skills/import',
            path: {
                'project_id': projectId,
            },
            formData: formData,
            mediaType: 'multipart/form-data',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Project Delete Skill
     * @returns void
     * @throws ApiError
     */
    public static projectDeleteSkillApiProjectsProjectIdSkillsSkillIdDelete({
        projectId,
        skillId,
        ifMatch,
    }: {
        projectId: string,
        skillId: string,
        ifMatch?: (string | null),
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/projects/{project_id}/skills/{skill_id}',
            path: {
                'project_id': projectId,
                'skill_id': skillId,
            },
            headers: {
                'If-Match': ifMatch,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Project Get Skill
     * @returns SkillOut Successful Response
     * @throws ApiError
     */
    public static projectGetSkillApiProjectsProjectIdSkillsSkillIdGet({
        projectId,
        skillId,
    }: {
        projectId: string,
        skillId: string,
    }): CancelablePromise<SkillOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/projects/{project_id}/skills/{skill_id}',
            path: {
                'project_id': projectId,
                'skill_id': skillId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Project Patch Skill
     * @returns SkillOut Successful Response
     * @throws ApiError
     */
    public static projectPatchSkillApiProjectsProjectIdSkillsSkillIdPatch({
        projectId,
        skillId,
        requestBody,
        ifMatch,
    }: {
        projectId: string,
        skillId: string,
        requestBody: SkillPatchIn,
        ifMatch?: (string | null),
    }): CancelablePromise<SkillOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/projects/{project_id}/skills/{skill_id}',
            path: {
                'project_id': projectId,
                'skill_id': skillId,
            },
            headers: {
                'If-Match': ifMatch,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Project Copy Skill
     * @returns SkillOut Successful Response
     * @throws ApiError
     */
    public static projectCopySkillApiProjectsProjectIdSkillsSkillIdCopyPost({
        projectId,
        skillId,
        requestBody,
    }: {
        projectId: string,
        skillId: string,
        requestBody: SkillCopyIn,
    }): CancelablePromise<SkillOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/projects/{project_id}/skills/{skill_id}/copy',
            path: {
                'project_id': projectId,
                'skill_id': skillId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Project Export Bundle
     * @returns BundleJobOut Successful Response
     * @throws ApiError
     */
    public static projectExportBundleApiProjectsProjectIdSkillsSkillIdExportGet({
        projectId,
        skillId,
    }: {
        projectId: string,
        skillId: string,
    }): CancelablePromise<BundleJobOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/projects/{project_id}/skills/{skill_id}/export',
            path: {
                'project_id': projectId,
                'skill_id': skillId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Project List Files
     * @returns SkillFileOut Successful Response
     * @throws ApiError
     */
    public static projectListFilesApiProjectsProjectIdSkillsSkillIdFilesGet({
        projectId,
        skillId,
    }: {
        projectId: string,
        skillId: string,
    }): CancelablePromise<Array<SkillFileOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/projects/{project_id}/skills/{skill_id}/files',
            path: {
                'project_id': projectId,
                'skill_id': skillId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Project Create File
     * @returns SkillFileOut Successful Response
     * @throws ApiError
     */
    public static projectCreateFileApiProjectsProjectIdSkillsSkillIdFilesPost({
        projectId,
        skillId,
        requestBody,
    }: {
        projectId: string,
        skillId: string,
        requestBody: SkillFileCreateIn,
    }): CancelablePromise<SkillFileOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/projects/{project_id}/skills/{skill_id}/files',
            path: {
                'project_id': projectId,
                'skill_id': skillId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Project Upload File
     * @returns SkillFileOut Successful Response
     * @throws ApiError
     */
    public static projectUploadFileApiProjectsProjectIdSkillsSkillIdFilesUploadPost({
        projectId,
        skillId,
        formData,
    }: {
        projectId: string,
        skillId: string,
        formData: Body_project_upload_file_api_projects__project_id__skills__skill_id__files_upload_post,
    }): CancelablePromise<SkillFileOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/projects/{project_id}/skills/{skill_id}/files/upload',
            path: {
                'project_id': projectId,
                'skill_id': skillId,
            },
            formData: formData,
            mediaType: 'multipart/form-data',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Project Delete File
     * @returns void
     * @throws ApiError
     */
    public static projectDeleteFileApiProjectsProjectIdSkillsSkillIdFilesFileIdDelete({
        projectId,
        skillId,
        fileId,
    }: {
        projectId: string,
        skillId: string,
        fileId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/projects/{project_id}/skills/{skill_id}/files/{file_id}',
            path: {
                'project_id': projectId,
                'skill_id': skillId,
                'file_id': fileId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Project Patch File
     * @returns SkillFileOut Successful Response
     * @throws ApiError
     */
    public static projectPatchFileApiProjectsProjectIdSkillsSkillIdFilesFileIdPatch({
        projectId,
        skillId,
        fileId,
        requestBody,
    }: {
        projectId: string,
        skillId: string,
        fileId: string,
        requestBody: SkillFilePatchIn,
    }): CancelablePromise<SkillFileOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/projects/{project_id}/skills/{skill_id}/files/{file_id}',
            path: {
                'project_id': projectId,
                'skill_id': skillId,
                'file_id': fileId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Project Restore Skill
     * @returns SkillOut Successful Response
     * @throws ApiError
     */
    public static projectRestoreSkillApiProjectsProjectIdSkillsSkillIdRestorePost({
        projectId,
        skillId,
    }: {
        projectId: string,
        skillId: string,
    }): CancelablePromise<SkillOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/projects/{project_id}/skills/{skill_id}/restore',
            path: {
                'project_id': projectId,
                'skill_id': skillId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Get Export Status
     * Poll a bundle export; once ready, carry the presigned download URL.
     *
     * §6 names only the import status endpoint by path; export equally needs a status/fetch
     * (D-58 records this). The URL dictates the download's filename and content type via the
     * presigned response headers, so the object's stored metadata never reaches the browser.
     * @returns BundleExportStatusOut Successful Response
     * @throws ApiError
     */
    public static getExportStatusApiSkillsExportsTaskIdGet({
        taskId,
    }: {
        taskId: string,
    }): CancelablePromise<BundleExportStatusOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/skills/exports/{task_id}',
            path: {
                'task_id': taskId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Get Import Status
     * Poll a bundle import. The initiator (or an admin) only — the job id is a capability.
     *
     * Deliberately not scope-gated: the initiator is recorded on the job and is the fence, the
     * same shape `GET /api/exports/{job_id}` uses. A stranger with a guessed id gets a 403, not
     * a leak of whether the id is live.
     * @returns BundleImportStatusOut Successful Response
     * @throws ApiError
     */
    public static getImportStatusApiSkillsImportsTaskIdGet({
        taskId,
    }: {
        taskId: string,
    }): CancelablePromise<BundleImportStatusOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/skills/imports/{task_id}',
            path: {
                'task_id': taskId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
}
