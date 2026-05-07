/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { RunOut } from '../models/RunOut';
import type { StepOut } from '../models/StepOut';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class WorkflowRunsService {
    /**
     * Get Run
     * @returns RunOut Successful Response
     * @throws ApiError
     */
    public static getRunApiWorkflowRunsRunIdGet({
        runId,
    }: {
        runId: string,
    }): CancelablePromise<RunOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/workflow-runs/{run_id}',
            path: {
                'run_id': runId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Cancel Run
     * @returns string Successful Response
     * @throws ApiError
     */
    public static cancelRunApiWorkflowRunsRunIdCancelPost({
        runId,
    }: {
        runId: string,
    }): CancelablePromise<Record<string, string>> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/workflow-runs/{run_id}/cancel',
            path: {
                'run_id': runId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Steps
     * @returns StepOut Successful Response
     * @throws ApiError
     */
    public static listStepsApiWorkflowRunsRunIdStepsGet({
        runId,
    }: {
        runId: string,
    }): CancelablePromise<Array<StepOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/workflow-runs/{run_id}/steps',
            path: {
                'run_id': runId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
