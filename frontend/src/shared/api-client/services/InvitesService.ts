/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AcceptByTokenIn } from '../models/AcceptByTokenIn';
import type { app__api__v1__invites__InviteOut } from '../models/app__api__v1__invites__InviteOut';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class InvitesService {
    /**
     * List Inbox
     * @returns app__api__v1__invites__InviteOut Successful Response
     * @throws ApiError
     */
    public static listInboxApiInvitesGet({
        state = 'pending',
        limit = 100,
        offset,
    }: {
        state?: 'pending' | 'accepted' | 'rejected',
        /**
         * Max items to return
         */
        limit?: number,
        /**
         * Number of items to skip
         */
        offset?: number,
    }): CancelablePromise<Array<app__api__v1__invites__InviteOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/invites',
            query: {
                'state': state,
                'limit': limit,
                'offset': offset,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Accept By Token
     * Redeem an invite from its emailed token link (R6.09).
     *
     * The token authorises acceptance (it proves the holder received the invite
     * mail), so no email match is required — but the caller must still be logged
     * in AND email-verified (R6.11), same as the by-id accept path.
     * @returns app__api__v1__invites__InviteOut Successful Response
     * @throws ApiError
     */
    public static acceptByTokenApiInvitesAcceptByTokenPost({
        requestBody,
    }: {
        requestBody: AcceptByTokenIn,
    }): CancelablePromise<app__api__v1__invites__InviteOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/invites/accept-by-token',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Accept
     * @returns app__api__v1__invites__InviteOut Successful Response
     * @throws ApiError
     */
    public static acceptApiInvitesInviteIdAcceptPost({
        inviteId,
    }: {
        inviteId: string,
    }): CancelablePromise<app__api__v1__invites__InviteOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/invites/{invite_id}/accept',
            path: {
                'invite_id': inviteId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Reject
     * @returns app__api__v1__invites__InviteOut Successful Response
     * @throws ApiError
     */
    public static rejectApiInvitesInviteIdRejectPost({
        inviteId,
    }: {
        inviteId: string,
    }): CancelablePromise<app__api__v1__invites__InviteOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/invites/{invite_id}/reject',
            path: {
                'invite_id': inviteId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
