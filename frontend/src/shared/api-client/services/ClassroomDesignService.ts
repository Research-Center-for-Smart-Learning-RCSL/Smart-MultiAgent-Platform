/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DesignApplyOut } from '../models/DesignApplyOut';
import type { DesignRunIn } from '../models/DesignRunIn';
import type { DesignRunOut } from '../models/DesignRunOut';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ClassroomDesignService {
    /**
     * List Design Runs
     * Newest first, so the panel can resume a run after a reload.
     * @returns DesignRunOut Successful Response
     * @throws ApiError
     */
    public static listDesignRunsApiProjectsProjectIdClassroomDesignRunsGet({
        projectId,
        limit = 10,
    }: {
        projectId: string,
        limit?: number,
    }): CancelablePromise<Array<DesignRunOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/projects/{project_id}/classroom-design/runs',
            path: {
                'project_id': projectId,
            },
            query: {
                'limit': limit,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Create Design Run
     * Queue a design run; 409 while another for this project is unfinished.
     * @returns DesignRunOut Successful Response
     * @throws ApiError
     */
    public static createDesignRunApiProjectsProjectIdClassroomDesignRunsPost({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: DesignRunIn,
    }): CancelablePromise<DesignRunOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/projects/{project_id}/classroom-design/runs',
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
     * Get Design Run
     * @returns DesignRunOut Successful Response
     * @throws ApiError
     */
    public static getDesignRunApiProjectsProjectIdClassroomDesignRunsRunIdGet({
        projectId,
        runId,
    }: {
        projectId: string,
        runId: string,
    }): CancelablePromise<DesignRunOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/projects/{project_id}/classroom-design/runs/{run_id}',
            path: {
                'project_id': projectId,
                'run_id': runId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Apply Design Run
     * Write both proposed prompts in this request's transaction, or neither.
     *
     * 409 ``agents/design-apply-conflict`` when either agent changed since the
     * run was drafted. The session rolls back, so nothing is written; the panel
     * asks for a new run, which drafts from the current prompts.
     * @returns DesignApplyOut Successful Response
     * @throws ApiError
     */
    public static applyDesignRunApiProjectsProjectIdClassroomDesignRunsRunIdApplyPost({
        projectId,
        runId,
    }: {
        projectId: string,
        runId: string,
    }): CancelablePromise<DesignApplyOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/projects/{project_id}/classroom-design/runs/{run_id}/apply',
            path: {
                'project_id': projectId,
                'run_id': runId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
}
