/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentCreateIn } from '../models/AgentCreateIn';
import type { AgentOut } from '../models/AgentOut';
import type { AgentPatchIn } from '../models/AgentPatchIn';
import type { AgentToolCreateIn } from '../models/AgentToolCreateIn';
import type { AgentToolOut } from '../models/AgentToolOut';
import type { AgentToolPatchIn } from '../models/AgentToolPatchIn';
import type { AgentToolTestOut } from '../models/AgentToolTestOut';
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
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
                422: `Validation Error`,
            },
        });
    }
}
