/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ArchivedRunOut } from '../models/ArchivedRunOut';
import type { RunOut } from '../models/RunOut';
import type { RunTriggerIn } from '../models/RunTriggerIn';
import type { ValidateIn } from '../models/ValidateIn';
import type { ValidateOut } from '../models/ValidateOut';
import type { WorkflowCreateIn } from '../models/WorkflowCreateIn';
import type { WorkflowOut } from '../models/WorkflowOut';
import type { WorkflowPatchIn } from '../models/WorkflowPatchIn';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class WorkflowsService {
    /**
     * Delete Workflow
     * @returns void
     * @throws ApiError
     */
    public static deleteWorkflowApiWorkflowsWorkflowIdDelete({
        workflowId,
    }: {
        workflowId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/workflows/{workflow_id}',
            path: {
                'workflow_id': workflowId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Patch Workflow
     * @returns WorkflowOut Successful Response
     * @throws ApiError
     */
    public static patchWorkflowApiWorkflowsWorkflowIdPatch({
        workflowId,
        ifMatch,
        requestBody,
    }: {
        workflowId: string,
        ifMatch: string,
        requestBody: WorkflowPatchIn,
    }): CancelablePromise<WorkflowOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/workflows/{workflow_id}',
            path: {
                'workflow_id': workflowId,
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
     * Dry Run
     * @returns string Successful Response
     * @throws ApiError
     */
    public static dryRunApiWorkflowsWorkflowIdDryRunPost({
        workflowId,
        requestBody,
    }: {
        workflowId: string,
        requestBody: RunTriggerIn,
    }): CancelablePromise<Record<string, string>> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/workflows/{workflow_id}/dry-run',
            path: {
                'workflow_id': workflowId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Runs
     * @returns any Successful Response
     * @throws ApiError
     */
    public static listRunsApiWorkflowsWorkflowIdRunsGet({
        workflowId,
        limit = 50,
        offset,
        includeArchive = false,
    }: {
        workflowId: string,
        limit?: number,
        offset?: number,
        includeArchive?: boolean,
    }): CancelablePromise<Array<(RunOut | ArchivedRunOut)>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/workflows/{workflow_id}/runs',
            path: {
                'workflow_id': workflowId,
            },
            query: {
                'limit': limit,
                'offset': offset,
                'include_archive': includeArchive,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Trigger Run
     * @returns string Successful Response
     * @throws ApiError
     */
    public static triggerRunApiWorkflowsWorkflowIdRunsPost({
        workflowId,
        requestBody,
    }: {
        workflowId: string,
        requestBody: RunTriggerIn,
    }): CancelablePromise<Record<string, string>> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/workflows/{workflow_id}/runs',
            path: {
                'workflow_id': workflowId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Workflows
     * @returns WorkflowOut Successful Response
     * @throws ApiError
     */
    public static listWorkflowsApiWorkspacesWidWorkflowsGet({
        wid,
        limit = 100,
        offset,
    }: {
        wid: string,
        /**
         * Max items to return
         */
        limit?: number,
        /**
         * Number of items to skip
         */
        offset?: number,
    }): CancelablePromise<Array<WorkflowOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/workspaces/{wid}/workflows',
            path: {
                'wid': wid,
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
     * Create Workflow
     * @returns WorkflowOut Successful Response
     * @throws ApiError
     */
    public static createWorkflowApiWorkspacesWidWorkflowsPost({
        wid,
        requestBody,
    }: {
        wid: string,
        requestBody: WorkflowCreateIn,
    }): CancelablePromise<WorkflowOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/workspaces/{wid}/workflows',
            path: {
                'wid': wid,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Validate Workflow
     * @returns ValidateOut Successful Response
     * @throws ApiError
     */
    public static validateWorkflowApiWorkspacesWidWorkflowsValidatePost({
        wid,
        requestBody,
    }: {
        wid: string,
        requestBody: ValidateIn,
    }): CancelablePromise<ValidateOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/workspaces/{wid}/workflows/validate',
            path: {
                'wid': wid,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
