/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SearchKeyIn } from '../models/SearchKeyIn';
import type { SearchKeyOut } from '../models/SearchKeyOut';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class SearchKeysService {
    /**
     * List Search Keys
     * @returns SearchKeyOut Successful Response
     * @throws ApiError
     */
    public static listSearchKeysApiProjectsProjectIdSearchKeysGet({
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
    }): CancelablePromise<Array<SearchKeyOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/projects/{project_id}/search-keys',
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
     * Upload Search Key
     * @returns SearchKeyOut Successful Response
     * @throws ApiError
     */
    public static uploadSearchKeyApiProjectsProjectIdSearchKeysPost({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: SearchKeyIn,
    }): CancelablePromise<SearchKeyOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/projects/{project_id}/search-keys',
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
     * Delete Search Key
     * @returns void
     * @throws ApiError
     */
    public static deleteSearchKeyApiProjectsProjectIdSearchKeysKeyIdDelete({
        projectId,
        keyId,
    }: {
        projectId: string,
        keyId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/projects/{project_id}/search-keys/{key_id}',
            path: {
                'project_id': projectId,
                'key_id': keyId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Activate Search Key
     * @returns void
     * @throws ApiError
     */
    public static activateSearchKeyApiProjectsProjectIdSearchKeysKeyIdActivatePost({
        projectId,
        keyId,
    }: {
        projectId: string,
        keyId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/projects/{project_id}/search-keys/{key_id}/activate',
            path: {
                'project_id': projectId,
                'key_id': keyId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Retest Search Key
     * @returns SearchKeyOut Successful Response
     * @throws ApiError
     */
    public static retestSearchKeyApiProjectsProjectIdSearchKeysKeyIdRetestPost({
        projectId,
        keyId,
    }: {
        projectId: string,
        keyId: string,
    }): CancelablePromise<SearchKeyOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/projects/{project_id}/search-keys/{key_id}/retest',
            path: {
                'project_id': projectId,
                'key_id': keyId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
