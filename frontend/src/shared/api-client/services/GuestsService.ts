/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class GuestsService {
    /**
     * Enroll Guest
     * @returns void
     * @throws ApiError
     */
    public static enrollGuestApiGuestChatroomIdGuestTokenEnrollPost({
        chatroomId,
        guestToken,
    }: {
        chatroomId: string,
        guestToken: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/guest/{chatroom_id}/{guest_token}/enroll',
            path: {
                'chatroom_id': chatroomId,
                'guest_token': guestToken,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
