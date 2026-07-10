/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentGroupCreateIn } from '../models/AgentGroupCreateIn';
import type { AgentGroupMemberIn } from '../models/AgentGroupMemberIn';
import type { AgentGroupMembersOut } from '../models/AgentGroupMembersOut';
import type { AgentGroupOut } from '../models/AgentGroupOut';
import type { AgentGroupUpdateIn } from '../models/AgentGroupUpdateIn';
import type { app__api__v1__agent_groups__ConceptMapStatusOut } from '../models/app__api__v1__agent_groups__ConceptMapStatusOut';
import type { ConceptMapEnabledIn } from '../models/ConceptMapEnabledIn';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class AgentGroupsService {
    /**
     * Delete Group
     * Delete an agent group (WS6 R11.20) — strict Project-Owner only.
     *
     * Purges the group-owned Concept Map's Neo4j subgraph + Qdrant points as part
     * of the delete so no external-store data is orphaned (AC-10). Member rows are
     * left in place; the group tombstone makes them inert on every read path.
     * @returns void
     * @throws ApiError
     */
    public static deleteGroupApiAgentGroupsGroupIdDelete({
        groupId,
    }: {
        groupId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/agent-groups/{group_id}',
            path: {
                'group_id': groupId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Group
     * Read a single agent group (Phase 4α). Read ⇒ project membership.
     * @returns AgentGroupOut Successful Response
     * @throws ApiError
     */
    public static getGroupApiAgentGroupsGroupIdGet({
        groupId,
    }: {
        groupId: string,
    }): CancelablePromise<AgentGroupOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/agent-groups/{group_id}',
            path: {
                'group_id': groupId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Rename Group
     * Rename an agent group (Phase 4α) — strict Project-Owner only.
     * @returns AgentGroupOut Successful Response
     * @throws ApiError
     */
    public static renameGroupApiAgentGroupsGroupIdPatch({
        groupId,
        requestBody,
    }: {
        groupId: string,
        requestBody: AgentGroupUpdateIn,
    }): CancelablePromise<AgentGroupOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/agent-groups/{group_id}',
            path: {
                'group_id': groupId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Set Concept Map Enabled
     * Toggle the group's Concept Map privacy opt-in (R11.10) — Project-Owner only.
     * @returns app__api__v1__agent_groups__ConceptMapStatusOut Successful Response
     * @throws ApiError
     */
    public static setConceptMapEnabledApiAgentGroupsGroupIdConceptMapEnabledPut({
        groupId,
        requestBody,
    }: {
        groupId: string,
        requestBody: ConceptMapEnabledIn,
    }): CancelablePromise<app__api__v1__agent_groups__ConceptMapStatusOut> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/agent-groups/{group_id}/concept-map-enabled',
            path: {
                'group_id': groupId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Members
     * @returns AgentGroupMembersOut Successful Response
     * @throws ApiError
     */
    public static listMembersApiAgentGroupsGroupIdMembersGet({
        groupId,
    }: {
        groupId: string,
    }): CancelablePromise<AgentGroupMembersOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/agent-groups/{group_id}/members',
            path: {
                'group_id': groupId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Add Member
     * @returns AgentGroupMembersOut Successful Response
     * @throws ApiError
     */
    public static addMemberApiAgentGroupsGroupIdMembersPost({
        groupId,
        requestBody,
    }: {
        groupId: string,
        requestBody: AgentGroupMemberIn,
    }): CancelablePromise<AgentGroupMembersOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/agent-groups/{group_id}/members',
            path: {
                'group_id': groupId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Remove Member
     * @returns void
     * @throws ApiError
     */
    public static removeMemberApiAgentGroupsGroupIdMembersAgentIdDelete({
        groupId,
        agentId,
    }: {
        groupId: string,
        agentId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/agent-groups/{group_id}/members/{agent_id}',
            path: {
                'group_id': groupId,
                'agent_id': agentId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Groups
     * List a project's agent groups (Phase 4α). Read ⇒ project membership.
     * @returns AgentGroupOut Successful Response
     * @throws ApiError
     */
    public static listGroupsApiProjectsProjectIdAgentGroupsGet({
        projectId,
        limit = 100,
        offset,
    }: {
        projectId: string,
        /**
         * Max items to return
         */
        limit?: number,
        /**
         * Number of items to skip
         */
        offset?: number,
    }): CancelablePromise<Array<AgentGroupOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/projects/{project_id}/agent-groups',
            path: {
                'project_id': projectId,
            },
            query: {
                'limit': limit,
                'offset': offset,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Group
     * @returns AgentGroupOut Successful Response
     * @throws ApiError
     */
    public static createGroupApiProjectsProjectIdAgentGroupsPost({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: AgentGroupCreateIn,
    }): CancelablePromise<AgentGroupOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/projects/{project_id}/agent-groups',
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
}
