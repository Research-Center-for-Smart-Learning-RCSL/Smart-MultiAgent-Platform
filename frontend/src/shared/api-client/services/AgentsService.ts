/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentCreateIn } from '../models/AgentCreateIn';
import type { AgentNameOut } from '../models/AgentNameOut';
import type { AgentOut } from '../models/AgentOut';
import type { AgentPatchIn } from '../models/AgentPatchIn';
import type { AgentToolCreateIn } from '../models/AgentToolCreateIn';
import type { AgentToolOut } from '../models/AgentToolOut';
import type { AgentToolPatchIn } from '../models/AgentToolPatchIn';
import type { AgentToolTestOut } from '../models/AgentToolTestOut';
import type { ExamplePackInstallIn } from '../models/ExamplePackInstallIn';
import type { ExamplePackInstallReportOut } from '../models/ExamplePackInstallReportOut';
import type { ExamplePackOut } from '../models/ExamplePackOut';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class AgentsService {
    /**
     * Delete Agent
     * @returns void
     * @throws ApiError
     */
    public static deleteAgentApiAgentsAgentIdDelete({
        agentId,
        ifMatch,
    }: {
        agentId: string,
        ifMatch: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/agents/{agent_id}',
            path: {
                'agent_id': agentId,
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
     * Read Agent
     * @returns AgentOut Successful Response
     * @throws ApiError
     */
    public static readAgentApiAgentsAgentIdGet({
        agentId,
    }: {
        agentId: string,
    }): CancelablePromise<AgentOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/agents/{agent_id}',
            path: {
                'agent_id': agentId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Patch Agent
     * @returns AgentOut Successful Response
     * @throws ApiError
     */
    public static patchAgentApiAgentsAgentIdPatch({
        agentId,
        ifMatch,
        requestBody,
    }: {
        agentId: string,
        ifMatch: string,
        requestBody: AgentPatchIn,
    }): CancelablePromise<AgentOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/agents/{agent_id}',
            path: {
                'agent_id': agentId,
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
     * List Agent Tools
     * @returns AgentToolOut Successful Response
     * @throws ApiError
     */
    public static listAgentToolsApiAgentsAgentIdToolsGet({
        agentId,
        limit = 100,
        offset,
    }: {
        agentId: string,
        /**
         * Max items to return
         */
        limit?: number,
        /**
         * Number of items to skip
         */
        offset?: number,
    }): CancelablePromise<Array<AgentToolOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/agents/{agent_id}/tools',
            path: {
                'agent_id': agentId,
            },
            query: {
                'limit': limit,
                'offset': offset,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Add Agent Tool
     * @returns AgentToolOut Successful Response
     * @throws ApiError
     */
    public static addAgentToolApiAgentsAgentIdToolsPost({
        agentId,
        requestBody,
    }: {
        agentId: string,
        requestBody: AgentToolCreateIn,
    }): CancelablePromise<AgentToolOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/agents/{agent_id}/tools',
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
     * Delete Agent Tool
     * @returns void
     * @throws ApiError
     */
    public static deleteAgentToolApiAgentsAgentIdToolsToolIdDelete({
        agentId,
        toolId,
    }: {
        agentId: string,
        toolId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/agents/{agent_id}/tools/{tool_id}',
            path: {
                'agent_id': agentId,
                'tool_id': toolId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Patch Agent Tool
     * @returns AgentToolOut Successful Response
     * @throws ApiError
     */
    public static patchAgentToolApiAgentsAgentIdToolsToolIdPatch({
        agentId,
        toolId,
        requestBody,
    }: {
        agentId: string,
        toolId: string,
        requestBody: AgentToolPatchIn,
    }): CancelablePromise<AgentToolOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/agents/{agent_id}/tools/{tool_id}',
            path: {
                'agent_id': agentId,
                'tool_id': toolId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Test Agent Tool
     * @returns AgentToolTestOut Successful Response
     * @throws ApiError
     */
    public static testAgentToolApiAgentsAgentIdToolsToolIdTestPost({
        agentId,
        toolId,
    }: {
        agentId: string,
        toolId: string,
    }): CancelablePromise<AgentToolTestOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/agents/{agent_id}/tools/{tool_id}/test',
            path: {
                'agent_id': agentId,
                'tool_id': toolId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * List Project Agents
     * @returns AgentOut Successful Response
     * @throws ApiError
     */
    public static listProjectAgentsApiProjectsProjectIdAgentsGet({
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
    }): CancelablePromise<Array<AgentOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/projects/{project_id}/agents',
            path: {
                'project_id': projectId,
            },
            query: {
                'limit': limit,
                'offset': offset,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Create Agent
     * @returns AgentOut Successful Response
     * @throws ApiError
     */
    public static createAgentApiProjectsProjectIdAgentsPost({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: AgentCreateIn,
    }): CancelablePromise<AgentOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/projects/{project_id}/agents',
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
     * List Agent Example Packs
     * The shipped packs and this project's install state ([R30.35]).
     *
     * Gated on `RESOURCE_CREATE_EDIT` rather than plain membership, matching
     * `create_agent`: the only thing this listing is for is deciding what to
     * install, and installing creates agents.
     * @returns ExamplePackOut Successful Response
     * @throws ApiError
     */
    public static listAgentExamplePacksApiProjectsProjectIdAgentsExamplePacksGet({
        projectId,
    }: {
        projectId: string,
    }): CancelablePromise<Array<ExamplePackOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/projects/{project_id}/agents/example-packs',
            path: {
                'project_id': projectId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Install Agent Example Pack
     * Instantiate a shipped pack into this project ([R30.35]).
     *
     * Creates agents and one agent group, nothing else: no chatroom, no room
     * binding, no activity started. `pack_key` is a client-supplied path segment,
     * which is what makes the loader's anchored traversal guard load-bearing here
     * rather than merely tidy.
     * @returns ExamplePackInstallReportOut Successful Response
     * @throws ApiError
     */
    public static installAgentExamplePackApiProjectsProjectIdAgentsExamplePacksPackKeyInstallPost({
        projectId,
        packKey,
        requestBody,
    }: {
        projectId: string,
        packKey: string,
        requestBody: ExamplePackInstallIn,
    }): CancelablePromise<ExamplePackInstallReportOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/projects/{project_id}/agents/example-packs/{pack_key}/install',
            path: {
                'project_id': projectId,
                'pack_key': packKey,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * List Project Agent Names
     * The same rows as `GET ""`, in the same order, projected to id and name.
     *
     * Deliberately the same membership gate: a name is not more sensitive than the
     * listing it is drawn from, and a second, looser gate on the same rows is how
     * an authorization surface drifts apart from itself.
     * @returns AgentNameOut Successful Response
     * @throws ApiError
     */
    public static listProjectAgentNamesApiProjectsProjectIdAgentsNamesGet({
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
    }): CancelablePromise<Array<AgentNameOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/projects/{project_id}/agents/names',
            path: {
                'project_id': projectId,
            },
            query: {
                'limit': limit,
                'offset': offset,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
}
