/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { WorkspaceCreatedOut } from '../models/WorkspaceCreatedOut';
import type { WorkspaceCreateIn } from '../models/WorkspaceCreateIn';
import type { WorkspaceOut } from '../models/WorkspaceOut';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class WorkspacesService {
    /**
     * List Workspaces
     * @returns WorkspaceOut Successful Response
     * @throws ApiError
     */
    public static listWorkspacesApiProjectsProjectIdWorkspacesGet({
        projectId,
    }: {
        projectId: string,
    }): CancelablePromise<Array<WorkspaceOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/projects/{project_id}/workspaces',
            path: {
                'project_id': projectId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Workspace
     * @returns WorkspaceCreatedOut Successful Response
     * @throws ApiError
     */
    public static createWorkspaceApiProjectsProjectIdWorkspacesPost({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: WorkspaceCreateIn,
    }): CancelablePromise<WorkspaceCreatedOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/projects/{project_id}/workspaces',
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
     * Delete Workspace
     * @returns void
     * @throws ApiError
     */
    public static deleteWorkspaceApiWorkspacesWorkspaceIdDelete({
        workspaceId,
    }: {
        workspaceId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/workspaces/{workspace_id}',
            path: {
                'workspace_id': workspaceId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
