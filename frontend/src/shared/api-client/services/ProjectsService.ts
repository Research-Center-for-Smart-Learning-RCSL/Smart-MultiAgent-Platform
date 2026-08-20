/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { app__api__v1__projects__InviteCreateIn } from '../models/app__api__v1__projects__InviteCreateIn';
import type { InvitableMemberOut } from '../models/InvitableMemberOut';
import type { ProjectCreateIn } from '../models/ProjectCreateIn';
import type { ProjectInviteOut } from '../models/ProjectInviteOut';
import type { ProjectMemberOut } from '../models/ProjectMemberOut';
import type { ProjectMemberPatchIn } from '../models/ProjectMemberPatchIn';
import type { ProjectOut } from '../models/ProjectOut';
import type { ProjectPatchIn } from '../models/ProjectPatchIn';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ProjectsService {
    /**
     * List Projects
     * @returns ProjectOut Successful Response
     * @throws ApiError
     */
    public static listProjectsApiProjectsGet({
        scope,
        id,
        limit = 100,
        offset,
    }: {
        scope?: ('user' | 'org' | null),
        id?: (string | null),
        /**
         * Max items to return
         */
        limit?: number,
        /**
         * Number of items to skip
         */
        offset?: number,
    }): CancelablePromise<Array<ProjectOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/projects',
            query: {
                'scope': scope,
                'id': id,
                'limit': limit,
                'offset': offset,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Create Project
     * @returns ProjectOut Successful Response
     * @throws ApiError
     */
    public static createProjectApiProjectsPost({
        requestBody,
    }: {
        requestBody: ProjectCreateIn,
    }): CancelablePromise<ProjectOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/projects',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Delete Project
     * @returns void
     * @throws ApiError
     */
    public static deleteProjectApiProjectsProjectIdDelete({
        projectId,
    }: {
        projectId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/projects/{project_id}',
            path: {
                'project_id': projectId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Read Project
     * @returns ProjectOut Successful Response
     * @throws ApiError
     */
    public static readProjectApiProjectsProjectIdGet({
        projectId,
    }: {
        projectId: string,
    }): CancelablePromise<ProjectOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/projects/{project_id}',
            path: {
                'project_id': projectId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Rename Project
     * @returns ProjectOut Successful Response
     * @throws ApiError
     */
    public static renameProjectApiProjectsProjectIdPatch({
        projectId,
        ifMatch,
        requestBody,
    }: {
        projectId: string,
        ifMatch: string,
        requestBody: ProjectPatchIn,
    }): CancelablePromise<ProjectOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/projects/{project_id}',
            path: {
                'project_id': projectId,
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
     * List Invitable Members
     * Parent-Org members this project may still invite (R6.10, Q-6).
     *
     * A user-owned project has no parent Org, so the pool is empty; that is a 200
     * with `[]`, never a 404 — an empty pool is a state, not a missing resource.
     * @returns InvitableMemberOut Successful Response
     * @throws ApiError
     */
    public static listInvitableMembersApiProjectsProjectIdInvitableMembersGet({
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
    }): CancelablePromise<Array<InvitableMemberOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/projects/{project_id}/invitable-members',
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
     * Create Project Invite
     * @returns ProjectInviteOut Successful Response
     * @throws ApiError
     */
    public static createProjectInviteApiProjectsProjectIdInvitesPost({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: app__api__v1__projects__InviteCreateIn,
    }): CancelablePromise<ProjectInviteOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/projects/{project_id}/invites',
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
     * List Members
     * @returns ProjectMemberOut Successful Response
     * @throws ApiError
     */
    public static listMembersApiProjectsProjectIdMembersGet({
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
    }): CancelablePromise<Array<ProjectMemberOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/projects/{project_id}/members',
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
     * Remove Project Member
     * @returns void
     * @throws ApiError
     */
    public static removeProjectMemberApiProjectsProjectIdMembersUserIdDelete({
        projectId,
        userId,
    }: {
        projectId: string,
        userId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/projects/{project_id}/members/{user_id}',
            path: {
                'project_id': projectId,
                'user_id': userId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Patch Project Member
     * @returns string Successful Response
     * @throws ApiError
     */
    public static patchProjectMemberApiProjectsProjectIdMembersUserIdPatch({
        projectId,
        userId,
        requestBody,
    }: {
        projectId: string,
        userId: string,
        requestBody: ProjectMemberPatchIn,
    }): CancelablePromise<Record<string, string>> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/projects/{project_id}/members/{user_id}',
            path: {
                'project_id': projectId,
                'user_id': userId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Restore Project
     * @returns void
     * @throws ApiError
     */
    public static restoreProjectApiProjectsProjectIdRestorePost({
        projectId,
    }: {
        projectId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/projects/{project_id}/restore',
            path: {
                'project_id': projectId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
}
