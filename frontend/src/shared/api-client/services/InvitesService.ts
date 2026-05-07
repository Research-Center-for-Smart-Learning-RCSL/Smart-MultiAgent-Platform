/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
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
    }: {
        state?: 'pending' | 'accepted' | 'rejected',
    }): CancelablePromise<Array<app__api__v1__invites__InviteOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/invites',
            query: {
                'state': state,
            },
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
