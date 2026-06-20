/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AddMemberIn } from '../models/AddMemberIn';
import type { app__api__v1__key_groups__MemberPatchIn } from '../models/app__api__v1__key_groups__MemberPatchIn';
import type { GroupDetailOut } from '../models/GroupDetailOut';
import type { GroupIn } from '../models/GroupIn';
import type { GroupOut } from '../models/GroupOut';
import type { GroupPatchIn } from '../models/GroupPatchIn';
import type { MemberOut } from '../models/MemberOut';
import type { ReorderIn } from '../models/ReorderIn';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class KeyGroupsService {
    /**
     * Delete Group
     * @returns void
     * @throws ApiError
     */
    public static deleteGroupApiKeyGroupsGroupIdDelete({
        groupId,
    }: {
        groupId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/key-groups/{group_id}',
            path: {
                'group_id': groupId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Read Group
     * @returns GroupDetailOut Successful Response
     * @throws ApiError
     */
    public static readGroupApiKeyGroupsGroupIdGet({
        groupId,
    }: {
        groupId: string,
    }): CancelablePromise<GroupDetailOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/key-groups/{group_id}',
            path: {
                'group_id': groupId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Rename Group
     * @returns void
     * @throws ApiError
     */
    public static renameGroupApiKeyGroupsGroupIdPatch({
        groupId,
        requestBody,
    }: {
        groupId: string,
        requestBody: GroupPatchIn,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/key-groups/{group_id}',
            path: {
                'group_id': groupId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Add Member
     * @returns MemberOut Successful Response
     * @throws ApiError
     */
    public static addMemberApiKeyGroupsGroupIdKeysPost({
        groupId,
        requestBody,
    }: {
        groupId: string,
        requestBody: AddMemberIn,
    }): CancelablePromise<MemberOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/key-groups/{group_id}/keys',
            path: {
                'group_id': groupId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Remove Member
     * @returns void
     * @throws ApiError
     */
    public static removeMemberApiKeyGroupsGroupIdKeysKeyIdDelete({
        groupId,
        keyId,
    }: {
        groupId: string,
        keyId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/key-groups/{group_id}/keys/{key_id}',
            path: {
                'group_id': groupId,
                'key_id': keyId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Patch Member
     * @returns void
     * @throws ApiError
     */
    public static patchMemberApiKeyGroupsGroupIdKeysKeyIdPatch({
        groupId,
        keyId,
        requestBody,
    }: {
        groupId: string,
        keyId: string,
        requestBody: app__api__v1__key_groups__MemberPatchIn,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/key-groups/{group_id}/keys/{key_id}',
            path: {
                'group_id': groupId,
                'key_id': keyId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Reorder Members
     * @returns void
     * @throws ApiError
     */
    public static reorderMembersApiKeyGroupsGroupIdReorderPost({
        groupId,
        requestBody,
    }: {
        groupId: string,
        requestBody: ReorderIn,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/key-groups/{group_id}/reorder',
            path: {
                'group_id': groupId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Groups
     * @returns GroupOut Successful Response
     * @throws ApiError
     */
    public static listGroupsApiProjectsProjectIdKeyGroupsGet({
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
    }): CancelablePromise<Array<GroupOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/projects/{project_id}/key-groups',
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
     * Create Group
     * @returns GroupOut Successful Response
     * @throws ApiError
     */
    public static createGroupApiProjectsProjectIdKeyGroupsPost({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: GroupIn,
    }): CancelablePromise<GroupOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/projects/{project_id}/key-groups',
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
