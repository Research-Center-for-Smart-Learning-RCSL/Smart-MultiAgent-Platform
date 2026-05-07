/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentCreateIn } from '../models/AgentCreateIn';
import type { AgentOut } from '../models/AgentOut';
import type { AgentPatchIn } from '../models/AgentPatchIn';
import type { McpBindingCreateIn } from '../models/McpBindingCreateIn';
import type { McpBindingOut } from '../models/McpBindingOut';
import type { McpBindingPatchIn } from '../models/McpBindingPatchIn';
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
     * List Mcp Bindings
     * @returns McpBindingOut Successful Response
     * @throws ApiError
     */
    public static listMcpBindingsApiAgentsAgentIdMcpGet({
        agentId,
    }: {
        agentId: string,
    }): CancelablePromise<Array<McpBindingOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/agents/{agent_id}/mcp',
            path: {
                'agent_id': agentId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Add Mcp Binding
     * @returns McpBindingOut Successful Response
     * @throws ApiError
     */
    public static addMcpBindingApiAgentsAgentIdMcpPost({
        agentId,
        requestBody,
    }: {
        agentId: string,
        requestBody: McpBindingCreateIn,
    }): CancelablePromise<McpBindingOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/agents/{agent_id}/mcp',
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
     * Delete Mcp Binding
     * @returns void
     * @throws ApiError
     */
    public static deleteMcpBindingApiAgentsAgentIdMcpBindingIdDelete({
        agentId,
        bindingId,
    }: {
        agentId: string,
        bindingId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/agents/{agent_id}/mcp/{binding_id}',
            path: {
                'agent_id': agentId,
                'binding_id': bindingId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Patch Mcp Binding
     * @returns McpBindingOut Successful Response
     * @throws ApiError
     */
    public static patchMcpBindingApiAgentsAgentIdMcpBindingIdPatch({
        agentId,
        bindingId,
        requestBody,
    }: {
        agentId: string,
        bindingId: string,
        requestBody: McpBindingPatchIn,
    }): CancelablePromise<McpBindingOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/agents/{agent_id}/mcp/{binding_id}',
            path: {
                'agent_id': agentId,
                'binding_id': bindingId,
            },
            body: requestBody,
            mediaType: 'application/json',
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
    }: {
        projectId: string,
    }): CancelablePromise<Array<AgentOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/projects/{project_id}/agents',
            path: {
                'project_id': projectId,
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
