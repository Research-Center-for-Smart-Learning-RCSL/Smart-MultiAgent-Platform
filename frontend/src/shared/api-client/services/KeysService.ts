/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CarryIn } from '../models/CarryIn';
import type { KeyListOut } from '../models/KeyListOut';
import type { KeyOut } from '../models/KeyOut';
import type { KeyProjectOut } from '../models/KeyProjectOut';
import type { KeyUploadIn } from '../models/KeyUploadIn';
import type { UsageOut } from '../models/UsageOut';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class KeysService {
    /**
     * List My Keys
     * List every key the caller owns (masked, no secrets).
     * @returns KeyListOut Successful Response
     * @throws ApiError
     */
    public static listMyKeysApiKeysGet({
        limit = 100,
        offset,
    }: {
        /**
         * Max items to return
         */
        limit?: number,
        /**
         * Number of items to skip
         */
        offset?: number,
    }): CancelablePromise<Array<KeyListOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/keys',
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
     * Upload Key
     * Upload a new provider key (§7.2 flow).
     *
     * AuthZ: KEY_UPLOAD is granted to any role carrying a user scope (§5.2 #2).
     * There is no path-param scope here; we still run the decision through the
     * matrix so admin-bypass + email-verification policy apply uniformly. The
     * matrix row accepts any non-guest role, so we only need the principal to
     * have *some* role — i.e. be a logged-in user. The `current_principal`
     * dependency already enforces that.
     * @returns KeyOut Successful Response
     * @throws ApiError
     */
    public static uploadKeyApiKeysPost({
        requestBody,
    }: {
        requestBody: KeyUploadIn,
    }): CancelablePromise<KeyOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/keys',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Key
     * Soft-delete a key. Cascades to Key-Group membership via ON DELETE.
     * @returns void
     * @throws ApiError
     */
    public static deleteKeyApiKeysKeyIdDelete({
        keyId,
    }: {
        keyId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/keys/{key_id}',
            path: {
                'key_id': keyId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Key Projects
     * Reverse view: which projects this key is carried into (owner-only).
     *
     * Ownership is enforced inside `KeysFacade.projects_for_key`. Results are
     * filtered to projects the caller is still a member of — a key owner who has
     * left a project must not see that project's data even if the carry revocation
     * fanout had not yet run (R7.03 / CLAUDE.md AuthZ rule).
     * @returns KeyProjectOut Successful Response
     * @throws ApiError
     */
    public static listKeyProjectsApiKeysKeyIdProjectsGet({
        keyId,
    }: {
        keyId: string,
    }): CancelablePromise<Array<KeyProjectOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/keys/{key_id}/projects',
            path: {
                'key_id': keyId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Retest Key
     * Re-run the provider probe against the stored key.
     * @returns KeyOut Successful Response
     * @throws ApiError
     */
    public static retestKeyApiKeysKeyIdRetestPost({
        keyId,
    }: {
        keyId: string,
    }): CancelablePromise<KeyOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/keys/{key_id}/retest',
            path: {
                'key_id': keyId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Carried Keys
     * @returns KeyOut Successful Response
     * @throws ApiError
     */
    public static listCarriedKeysApiProjectsProjectIdKeysGet({
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
    }): CancelablePromise<Array<KeyOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/projects/{project_id}/keys',
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
     * Carry Key
     * @returns void
     * @throws ApiError
     */
    public static carryKeyApiProjectsProjectIdKeysPost({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: CarryIn,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/projects/{project_id}/keys',
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
     * Withdraw Key
     * @returns void
     * @throws ApiError
     */
    public static withdrawKeyApiProjectsProjectIdKeysKeyIdDelete({
        projectId,
        keyId,
    }: {
        projectId: string,
        keyId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/projects/{project_id}/keys/{key_id}',
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
     * Read Usage
     * @returns UsageOut Successful Response
     * @throws ApiError
     */
    public static readUsageApiProjectsProjectIdKeysKeyIdUsageGet({
        projectId,
        keyId,
        window = '1h',
    }: {
        projectId: string,
        keyId: string,
        window?: '1h' | '24h' | '7d' | '30d',
    }): CancelablePromise<UsageOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/projects/{project_id}/keys/{key_id}/usage',
            path: {
                'project_id': projectId,
                'key_id': keyId,
            },
            query: {
                'window': window,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
