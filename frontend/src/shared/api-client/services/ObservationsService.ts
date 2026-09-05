/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ObservationOut } from '../models/ObservationOut';
import type { ReleaseIn } from '../models/ReleaseIn';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ObservationsService {
    /**
     * List Observations
     * @returns ObservationOut Successful Response
     * @throws ApiError
     */
    public static listObservationsApiChatroomsChatroomIdObservationsGet({
        chatroomId,
        before,
        limit = 50,
    }: {
        chatroomId: string,
        before?: (string | null),
        limit?: number,
    }): CancelablePromise<Array<ObservationOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/chatrooms/{chatroom_id}/observations',
            path: {
                'chatroom_id': chatroomId,
            },
            query: {
                'before': before,
                'limit': limit,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Delete Observation
     * @returns void
     * @throws ApiError
     */
    public static deleteObservationApiChatroomsChatroomIdObservationsObservationIdDelete({
        chatroomId,
        observationId,
    }: {
        chatroomId: string,
        observationId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/chatrooms/{chatroom_id}/observations/{observation_id}',
            path: {
                'chatroom_id': chatroomId,
                'observation_id': observationId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Release Observation
     * @returns ObservationOut Successful Response
     * @throws ApiError
     */
    public static releaseObservationApiChatroomsChatroomIdObservationsObservationIdReleasePost({
        chatroomId,
        observationId,
        requestBody,
    }: {
        chatroomId: string,
        observationId: string,
        requestBody: ReleaseIn,
    }): CancelablePromise<ObservationOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/chatrooms/{chatroom_id}/observations/{observation_id}/release',
            path: {
                'chatroom_id': chatroomId,
                'observation_id': observationId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
}
