/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { app__api__v1__orgs__InviteCreateIn } from '../models/app__api__v1__orgs__InviteCreateIn';
import type { app__api__v1__orgs__InviteOut } from '../models/app__api__v1__orgs__InviteOut';
import type { app__api__v1__orgs__MemberPatchIn } from '../models/app__api__v1__orgs__MemberPatchIn';
import type { OrgCreateIn } from '../models/OrgCreateIn';
import type { OrgMemberOut } from '../models/OrgMemberOut';
import type { OrgOut } from '../models/OrgOut';
import type { OrgPatchIn } from '../models/OrgPatchIn';
import type { OrgQuotasOut } from '../models/OrgQuotasOut';
import type { TransferInitIn } from '../models/TransferInitIn';
import type { TransferOut } from '../models/TransferOut';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class OrgsService {
    /**
     * List Orgs
     * @returns OrgOut Successful Response
     * @throws ApiError
     */
    public static listOrgsApiOrgsGet({
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
    }): CancelablePromise<Array<OrgOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/orgs',
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
     * Create Org
     * @returns OrgOut Successful Response
     * @throws ApiError
     */
    public static createOrgApiOrgsPost({
        requestBody,
    }: {
        requestBody: OrgCreateIn,
    }): CancelablePromise<OrgOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/orgs',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Org
     * @returns void
     * @throws ApiError
     */
    public static deleteOrgApiOrgsOrgIdDelete({
        orgId,
    }: {
        orgId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/orgs/{org_id}',
            path: {
                'org_id': orgId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Read Org
     * @returns OrgOut Successful Response
     * @throws ApiError
     */
    public static readOrgApiOrgsOrgIdGet({
        orgId,
    }: {
        orgId: string,
    }): CancelablePromise<OrgOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/orgs/{org_id}',
            path: {
                'org_id': orgId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Rename Org
     * @returns OrgOut Successful Response
     * @throws ApiError
     */
    public static renameOrgApiOrgsOrgIdPatch({
        orgId,
        ifMatch,
        requestBody,
    }: {
        orgId: string,
        ifMatch: string,
        requestBody: OrgPatchIn,
    }): CancelablePromise<OrgOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/orgs/{org_id}',
            path: {
                'org_id': orgId,
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
     * Create Invite
     * @returns app__api__v1__orgs__InviteOut Successful Response
     * @throws ApiError
     */
    public static createInviteApiOrgsOrgIdInvitesPost({
        orgId,
        requestBody,
    }: {
        orgId: string,
        requestBody: app__api__v1__orgs__InviteCreateIn,
    }): CancelablePromise<app__api__v1__orgs__InviteOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/orgs/{org_id}/invites',
            path: {
                'org_id': orgId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Members
     * @returns OrgMemberOut Successful Response
     * @throws ApiError
     */
    public static listMembersApiOrgsOrgIdMembersGet({
        orgId,
        limit = 100,
        offset,
    }: {
        orgId: string,
        /**
         * Max items to return
         */
        limit?: number,
        /**
         * Number of items to skip
         */
        offset?: number,
    }): CancelablePromise<Array<OrgMemberOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/orgs/{org_id}/members',
            path: {
                'org_id': orgId,
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
     * Remove Member
     * @returns void
     * @throws ApiError
     */
    public static removeMemberApiOrgsOrgIdMembersUserIdDelete({
        orgId,
        userId,
    }: {
        orgId: string,
        userId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/orgs/{org_id}/members/{user_id}',
            path: {
                'org_id': orgId,
                'user_id': userId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Patch Member
     * @returns string Successful Response
     * @throws ApiError
     */
    public static patchMemberApiOrgsOrgIdMembersUserIdPatch({
        orgId,
        userId,
        requestBody,
    }: {
        orgId: string,
        userId: string,
        requestBody: app__api__v1__orgs__MemberPatchIn,
    }): CancelablePromise<Record<string, string>> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/orgs/{org_id}/members/{user_id}',
            path: {
                'org_id': orgId,
                'user_id': userId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Transfer List
     * @returns TransferOut Successful Response
     * @throws ApiError
     */
    public static transferListApiOrgsOrgIdOriginalCreatorTransfersGet({
        orgId,
        limit = 100,
        offset,
    }: {
        orgId: string,
        /**
         * Max items to return
         */
        limit?: number,
        /**
         * Number of items to skip
         */
        offset?: number,
    }): CancelablePromise<Array<TransferOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/orgs/{org_id}/original-creator-transfers',
            path: {
                'org_id': orgId,
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
     * Transfer Initiate
     * @returns TransferOut Successful Response
     * @throws ApiError
     */
    public static transferInitiateApiOrgsOrgIdOriginalCreatorTransfersPost({
        orgId,
        requestBody,
    }: {
        orgId: string,
        requestBody: TransferInitIn,
    }): CancelablePromise<TransferOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/orgs/{org_id}/original-creator-transfers',
            path: {
                'org_id': orgId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Transfer Cancel
     * @returns void
     * @throws ApiError
     */
    public static transferCancelApiOrgsOrgIdOriginalCreatorTransfersTransferIdDelete({
        orgId,
        transferId,
    }: {
        orgId: string,
        transferId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/orgs/{org_id}/original-creator-transfers/{transfer_id}',
            path: {
                'org_id': orgId,
                'transfer_id': transferId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Transfer Accept
     * @returns TransferOut Successful Response
     * @throws ApiError
     */
    public static transferAcceptApiOrgsOrgIdOriginalCreatorTransfersTransferIdAcceptPost({
        orgId,
        transferId,
    }: {
        orgId: string,
        transferId: string,
    }): CancelablePromise<TransferOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/orgs/{org_id}/original-creator-transfers/{transfer_id}/accept',
            path: {
                'org_id': orgId,
                'transfer_id': transferId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Transfer Reject
     * @returns void
     * @throws ApiError
     */
    public static transferRejectApiOrgsOrgIdOriginalCreatorTransfersTransferIdRejectPost({
        orgId,
        transferId,
    }: {
        orgId: string,
        transferId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/orgs/{org_id}/original-creator-transfers/{transfer_id}/reject',
            path: {
                'org_id': orgId,
                'transfer_id': transferId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Org Quotas
     * @returns OrgQuotasOut Successful Response
     * @throws ApiError
     */
    public static getOrgQuotasApiOrgsOrgIdQuotasGet({
        orgId,
    }: {
        orgId: string,
    }): CancelablePromise<OrgQuotasOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/orgs/{org_id}/quotas',
            path: {
                'org_id': orgId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Restore Org
     * @returns void
     * @throws ApiError
     */
    public static restoreOrgApiOrgsOrgIdRestorePost({
        orgId,
    }: {
        orgId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/orgs/{org_id}/restore',
            path: {
                'org_id': orgId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
