/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { MarkReadIn } from '../models/MarkReadIn';
import type { MarkReadOut } from '../models/MarkReadOut';
import type { NotificationOut } from '../models/NotificationOut';
import type { UnreadCountOut } from '../models/UnreadCountOut';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class NotificationsService {
    /**
     * List Notifications
     * @returns NotificationOut Successful Response
     * @throws ApiError
     */
    public static listNotificationsApiNotificationsGet({
        cursor,
        limit = 50,
    }: {
        cursor?: (string | null),
        limit?: number,
    }): CancelablePromise<Array<NotificationOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/notifications',
            query: {
                'cursor': cursor,
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Mark Read
     * @returns MarkReadOut Successful Response
     * @throws ApiError
     */
    public static markReadApiNotificationsReadPost({
        requestBody,
    }: {
        requestBody: MarkReadIn,
    }): CancelablePromise<MarkReadOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/notifications/read',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Unread Count
     * @returns UnreadCountOut Successful Response
     * @throws ApiError
     */
    public static unreadCountApiNotificationsUnreadCountGet(): CancelablePromise<UnreadCountOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/notifications/unread-count',
        });
    }
}
