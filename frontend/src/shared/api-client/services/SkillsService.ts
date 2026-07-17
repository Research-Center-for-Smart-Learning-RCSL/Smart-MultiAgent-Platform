/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Body_admin_upload_file_api_admin_skills__skill_id__files_upload_post } from '../models/Body_admin_upload_file_api_admin_skills__skill_id__files_upload_post';
import type { Body_agent_upload_file_api_agents__agent_id__skills__skill_id__files_upload_post } from '../models/Body_agent_upload_file_api_agents__agent_id__skills__skill_id__files_upload_post';
import type { Body_org_upload_file_api_orgs__org_id__skills__skill_id__files_upload_post } from '../models/Body_org_upload_file_api_orgs__org_id__skills__skill_id__files_upload_post';
import type { Body_project_upload_file_api_projects__project_id__skills__skill_id__files_upload_post } from '../models/Body_project_upload_file_api_projects__project_id__skills__skill_id__files_upload_post';
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
            },
        });
    }
}
