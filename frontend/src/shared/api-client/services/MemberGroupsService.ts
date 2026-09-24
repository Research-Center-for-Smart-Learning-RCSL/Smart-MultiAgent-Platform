/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { MemberGroupCreateIn } from '../models/MemberGroupCreateIn';
import type { MemberGroupMemberIn } from '../models/MemberGroupMemberIn';
import type { MemberGroupMemberOut } from '../models/MemberGroupMemberOut';
import type { MemberGroupOut } from '../models/MemberGroupOut';
import type { MemberGroupPatchIn } from '../models/MemberGroupPatchIn';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class MemberGroupsService {
    /**
     * Delete Member Group
     * @returns void
     * @throws ApiError
     */
    public static deleteMemberGroupApiMemberGroupsGroupIdDelete({
        groupId,
    }: {
        groupId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/member-groups/{group_id}',
            path: {
                'group_id': groupId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Read Member Group
     * @returns MemberGroupOut Successful Response
     * @throws ApiError
     */
    public static readMemberGroupApiMemberGroupsGroupIdGet({
        groupId,
    }: {
        groupId: string,
    }): CancelablePromise<MemberGroupOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/member-groups/{group_id}',
            path: {
                'group_id': groupId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Rename Member Group
     * @returns MemberGroupOut Successful Response
     * @throws ApiError
     */
    public static renameMemberGroupApiMemberGroupsGroupIdPatch({
        groupId,
        ifMatch,
        requestBody,
    }: {
        groupId: string,
        ifMatch: string,
        requestBody: MemberGroupPatchIn,
    }): CancelablePromise<MemberGroupOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/member-groups/{group_id}',
            path: {
                'group_id': groupId,
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
     * List Member Group Members
     * A member of the group may see who else is in it; anyone else cannot see
     * that the group exists at all (R13.31).
     * @returns MemberGroupMemberOut Successful Response
     * @throws ApiError
     */
    public static listMemberGroupMembersApiMemberGroupsGroupIdMembersGet({
        groupId,
    }: {
        groupId: string,
    }): CancelablePromise<Array<MemberGroupMemberOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/member-groups/{group_id}/members',
            path: {
                'group_id': groupId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Add Member Group Member
     * @returns void
     * @throws ApiError
     */
    public static addMemberGroupMemberApiMemberGroupsGroupIdMembersPost({
        groupId,
        requestBody,
    }: {
        groupId: string,
        requestBody: MemberGroupMemberIn,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/member-groups/{group_id}/members',
            path: {
                'group_id': groupId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Remove Member Group Member
     * @returns void
     * @throws ApiError
     */
    public static removeMemberGroupMemberApiMemberGroupsGroupIdMembersUserIdDelete({
        groupId,
        userId,
    }: {
        groupId: string,
        userId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/member-groups/{group_id}/members/{user_id}',
            path: {
                'group_id': groupId,
                'user_id': userId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * List Member Groups
     * R13.31 — a manager sees the project's groups, anyone else sees their own.
     * @returns MemberGroupOut Successful Response
     * @throws ApiError
     */
    public static listMemberGroupsApiProjectsProjectIdMemberGroupsGet({
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
    }): CancelablePromise<Array<MemberGroupOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/projects/{project_id}/member-groups',
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
     * Create Member Group
     * @returns MemberGroupOut Successful Response
     * @throws ApiError
     */
    public static createMemberGroupApiProjectsProjectIdMemberGroupsPost({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: MemberGroupCreateIn,
    }): CancelablePromise<MemberGroupOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/projects/{project_id}/member-groups',
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
}
