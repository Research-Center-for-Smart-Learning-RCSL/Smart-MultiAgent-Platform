/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { app__api__v1__workspaces__ConceptMapStatusOut } from '../models/app__api__v1__workspaces__ConceptMapStatusOut';
import type { ConceptMapEnabledIn } from '../models/ConceptMapEnabledIn';
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
    }): CancelablePromise<Array<WorkspaceOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/projects/{project_id}/workspaces',
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
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Read Workspace
     * @returns WorkspaceOut Successful Response
     * @throws ApiError
     */
    public static readWorkspaceApiWorkspacesWorkspaceIdGet({
        workspaceId,
    }: {
        workspaceId: string,
    }): CancelablePromise<WorkspaceOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/workspaces/{workspace_id}',
            path: {
                'workspace_id': workspaceId,
            },
            errors: {
                422: `Request Validation Problem`,
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
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Set Concept Map Enabled
     * Toggle the workspace's Concept Map privacy opt-in (R11.10) — Project-Owner only.
     *
     * Enabling exposes the shared workspace map to retrieval, so this is gated on
     * strict Project-Owner authority (not the RESOURCE_CREATE_EDIT capability that
     * guards ordinary workspace edits), matching the group-owner toggle.
     * @returns app__api__v1__workspaces__ConceptMapStatusOut Successful Response
     * @throws ApiError
     */
    public static setConceptMapEnabledApiWorkspacesWorkspaceIdConceptMapEnabledPut({
        workspaceId,
        requestBody,
    }: {
        workspaceId: string,
        requestBody: ConceptMapEnabledIn,
    }): CancelablePromise<app__api__v1__workspaces__ConceptMapStatusOut> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/workspaces/{workspace_id}/concept-map-enabled',
            path: {
                'workspace_id': workspaceId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
}
