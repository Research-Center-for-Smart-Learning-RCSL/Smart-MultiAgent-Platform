/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { GuestDisplayNameIn } from '../models/GuestDisplayNameIn';
import type { GuestEnrollIn } from '../models/GuestEnrollIn';
import type { GuestRefreshOut } from '../models/GuestRefreshOut';
import type { GuestSessionIn } from '../models/GuestSessionIn';
import type { GuestSessionOut } from '../models/GuestSessionOut';
import type { GuestWsTicketOut } from '../models/GuestWsTicketOut';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class GuestsService {
    /**
     * Update Guest Display Name
     * @returns void
     * @throws ApiError
     */
    public static updateGuestDisplayNameApiGuestSessionGuestSessionIdDisplayNamePut({
        guestSessionId,
        requestBody,
    }: {
        guestSessionId: string,
        requestBody: GuestDisplayNameIn,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/guest/session/{guest_session_id}/display-name',
            path: {
                'guest_session_id': guestSessionId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Guest Ws Ticket
     * Mint a WS ticket for a guest. Requires a valid guest JWT in Bearer.
     * @returns GuestWsTicketOut Successful Response
     * @throws ApiError
     */
    public static guestWsTicketApiGuestWsTicketPost(): CancelablePromise<GuestWsTicketOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/guest/ws-ticket',
        });
    }
    /**
     * Refresh Guest Session
     * @returns GuestRefreshOut Successful Response
     * @throws ApiError
     */
    public static refreshGuestSessionApiGuestChatroomIdRefreshPost({
        chatroomId,
    }: {
        chatroomId: string,
    }): CancelablePromise<GuestRefreshOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/guest/{chatroom_id}/refresh',
            path: {
                'chatroom_id': chatroomId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Enroll Guest
     * @returns void
     * @throws ApiError
     */
    public static enrollGuestApiGuestChatroomIdGuestTokenEnrollPost({
        chatroomId,
        guestToken,
        requestBody,
    }: {
        chatroomId: string,
        guestToken: string,
        requestBody?: GuestEnrollIn,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/guest/{chatroom_id}/{guest_token}/enroll',
            path: {
                'chatroom_id': chatroomId,
                'guest_token': guestToken,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Create Guest Session
     * @returns GuestSessionOut Successful Response
     * @throws ApiError
     */
    public static createGuestSessionApiGuestChatroomIdGuestTokenSessionPost({
        chatroomId,
        guestToken,
        requestBody,
    }: {
        chatroomId: string,
        guestToken: string,
        requestBody: GuestSessionIn,
    }): CancelablePromise<GuestSessionOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/guest/{chatroom_id}/{guest_token}/session',
            path: {
                'chatroom_id': chatroomId,
                'guest_token': guestToken,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
}
