/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class OrchestrationService {
    /**
     * Read A2A DLQ entries for an agent
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getAgentDlqApiOrchestrationAgentsAgentIdDlqGet({
        agentId,
    }: {
        agentId: string,
    }): CancelablePromise<Array<Record<string, any>>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/orchestration/agents/{agent_id}/dlq',
            path: {
                'agent_id': agentId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get approval gate with votes
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getApprovalApiOrchestrationApprovalsApprovalIdGet({
        approvalId,
    }: {
        approvalId: string,
    }): CancelablePromise<Record<string, any>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/orchestration/approvals/{approval_id}',
            path: {
                'approval_id': approvalId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List all instructions in a chain
     * @returns any Successful Response
     * @throws ApiError
     */
    public static listInstructionsForChainApiOrchestrationChainsChainIdInstructionsGet({
        chainId,
        limit = 100,
        offset,
    }: {
        chainId: string,
        /**
         * Max items to return
         */
        limit?: number,
        /**
         * Number of items to skip
         */
        offset?: number,
    }): CancelablePromise<Array<Record<string, any>>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/orchestration/chains/{chain_id}/instructions',
            path: {
                'chain_id': chainId,
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
     * List live sub-agents for a parent instance
     * @returns any Successful Response
     * @throws ApiError
     */
    public static listSubagentChildrenApiOrchestrationInstancesParentInstanceIdChildrenGet({
        parentInstanceId,
        limit = 100,
        offset,
    }: {
        parentInstanceId: string,
        /**
         * Max items to return
         */
        limit?: number,
        /**
         * Number of items to skip
         */
        offset?: number,
    }): CancelablePromise<Array<Record<string, any>>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/orchestration/instances/{parent_instance_id}/children',
            path: {
                'parent_instance_id': parentInstanceId,
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
     * Get a single instruction record
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getInstructionApiOrchestrationInstructionsInstructionIdGet({
        instructionId,
    }: {
        instructionId: string,
    }): CancelablePromise<Record<string, any>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/orchestration/instructions/{instruction_id}',
            path: {
                'instruction_id': instructionId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List approvals for a workflow run
     * @returns any Successful Response
     * @throws ApiError
     */
    public static listApprovalsForRunApiOrchestrationWorkflowRunsWorkflowRunIdApprovalsGet({
        workflowRunId,
        limit = 100,
        offset,
    }: {
        workflowRunId: string,
        /**
         * Max items to return
         */
        limit?: number,
        /**
         * Number of items to skip
         */
        offset?: number,
    }): CancelablePromise<Array<Record<string, any>>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/orchestration/workflow-runs/{workflow_run_id}/approvals',
            path: {
                'workflow_run_id': workflowRunId,
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
     * List sub-agents spawned during a workflow run
     * @returns any Successful Response
     * @throws ApiError
     */
    public static listRunSubagentsApiOrchestrationWorkflowRunsWorkflowRunIdSubagentsGet({
        workflowRunId,
        limit = 100,
        offset,
    }: {
        workflowRunId: string,
        /**
         * Max items to return
         */
        limit?: number,
        /**
         * Number of items to skip
         */
        offset?: number,
    }): CancelablePromise<Array<Record<string, any>>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/orchestration/workflow-runs/{workflow_run_id}/subagents',
            path: {
                'workflow_run_id': workflowRunId,
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
}
