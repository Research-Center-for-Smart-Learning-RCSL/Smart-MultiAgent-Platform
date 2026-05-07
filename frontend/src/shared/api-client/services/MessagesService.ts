/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { MessageOut } from '../models/MessageOut';
import type { MessagePatchIn } from '../models/MessagePatchIn';
import type { MessageSendIn } from '../models/MessageSendIn';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class MessagesService {
    /**
     * List Messages
     * @returns MessageOut Successful Response
     * @throws ApiError
     */
    public static listMessagesApiChatroomsChatroomIdMessagesGet({
        chatroomId,
        before,
        since,
        limit = 50,
    }: {
        chatroomId: string,
        before?: (string | null),
        since?: (string | null),
        limit?: number,
    }): CancelablePromise<Array<MessageOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/chatrooms/{chatroom_id}/messages',
            path: {
                'chatroom_id': chatroomId,
            },
            query: {
                'before': before,
                'since': since,
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Send Message
     * @returns MessageOut Successful Response
     * @throws ApiError
     */
    public static sendMessageApiChatroomsChatroomIdMessagesPost({
        chatroomId,
        requestBody,
    }: {
        chatroomId: string,
        requestBody: MessageSendIn,
    }): CancelablePromise<MessageOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/chatrooms/{chatroom_id}/messages',
            path: {
                'chatroom_id': chatroomId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Message
     * @returns void
     * @throws ApiError
     */
    public static deleteMessageApiMessagesMessageIdDelete({
        messageId,
    }: {
        messageId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/messages/{message_id}',
            path: {
                'message_id': messageId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Read Message
     * @returns MessageOut Successful Response
     * @throws ApiError
     */
    public static readMessageApiMessagesMessageIdGet({
        messageId,
    }: {
        messageId: string,
    }): CancelablePromise<MessageOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/messages/{message_id}',
            path: {
                'message_id': messageId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Edit Message
     * @returns MessageOut Successful Response
     * @throws ApiError
     */
    public static editMessageApiMessagesMessageIdPatch({
        messageId,
        ifMatch,
        requestBody,
    }: {
        messageId: string,
        ifMatch: string,
        requestBody: MessagePatchIn,
    }): CancelablePromise<MessageOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/messages/{message_id}',
            path: {
                'message_id': messageId,
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
}
