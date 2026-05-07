/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SearchResponse } from '../models/SearchResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class SearchService {
    /**
     * Search Messages
     * @returns SearchResponse Successful Response
     * @throws ApiError
     */
    public static searchMessagesApiChatroomsChatroomIdSearchGet({
        chatroomId,
        q,
        limit = 50,
        offset,
    }: {
        chatroomId: string,
        q: string,
        limit?: number,
        offset?: number,
    }): CancelablePromise<SearchResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/chatrooms/{chatroom_id}/search',
            path: {
                'chatroom_id': chatroomId,
            },
            query: {
                'q': q,
                'limit': limit,
                'offset': offset,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
