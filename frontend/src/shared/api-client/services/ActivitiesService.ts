/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ActivitySessionOpenIn } from '../models/ActivitySessionOpenIn';
import type { ActivitySessionOut } from '../models/ActivitySessionOut';
import type { ActivitySubmissionIn } from '../models/ActivitySubmissionIn';
import type { ActivitySubmissionOut } from '../models/ActivitySubmissionOut';
import type { ActivitySubmissionsPageOut } from '../models/ActivitySubmissionsPageOut';
import type { ActivityTypeIn } from '../models/ActivityTypeIn';
import type { ActivityTypeOut } from '../models/ActivityTypeOut';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ActivitiesService {
    /**
     * Open Activity Session
     * @returns ActivitySessionOut Successful Response
     * @throws ApiError
     */
    public static openActivitySessionApiChatroomsChatroomIdActivitySessionsPost({
        chatroomId,
        requestBody,
    }: {
        chatroomId: string,
        requestBody: ActivitySessionOpenIn,
    }): CancelablePromise<ActivitySessionOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/chatrooms/{chatroom_id}/activity-sessions',
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
     * Close Activity Session
     * @returns ActivitySessionOut Successful Response
     * @throws ApiError
     */
    public static closeActivitySessionApiChatroomsChatroomIdActivitySessionsSessionIdClosePatch({
        chatroomId,
        sessionId,
    }: {
        chatroomId: string,
        sessionId: string,
    }): CancelablePromise<ActivitySessionOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/chatrooms/{chatroom_id}/activity-sessions/{session_id}/close',
            path: {
                'chatroom_id': chatroomId,
                'session_id': sessionId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Activity Submissions
     * @returns ActivitySubmissionsPageOut Successful Response
     * @throws ApiError
     */
    public static listActivitySubmissionsApiChatroomsChatroomIdActivitySubmissionsGet({
        chatroomId,
        sessionId,
        subjectUserId,
        limit = 100,
        offset,
    }: {
        chatroomId: string,
        sessionId?: (string | null),
        subjectUserId?: (string | null),
        /**
         * Max items to return
         */
        limit?: number,
        /**
         * Number of items to skip
         */
        offset?: number,
    }): CancelablePromise<ActivitySubmissionsPageOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/chatrooms/{chatroom_id}/activity-submissions',
            path: {
                'chatroom_id': chatroomId,
            },
            query: {
                'session_id': sessionId,
                'subject_user_id': subjectUserId,
                'limit': limit,
                'offset': offset,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Submit Activity
     * @returns ActivitySubmissionOut Successful Response
     * @throws ApiError
     */
    public static submitActivityApiChatroomsChatroomIdActivitySubmissionsPost({
        chatroomId,
        requestBody,
    }: {
        chatroomId: string,
        requestBody: ActivitySubmissionIn,
    }): CancelablePromise<ActivitySubmissionOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/chatrooms/{chatroom_id}/activity-submissions',
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
     * List Activity Types
     * @returns ActivityTypeOut Successful Response
     * @throws ApiError
     */
    public static listActivityTypesApiProjectsProjectIdActivityTypesGet({
        projectId,
    }: {
        projectId: string,
    }): CancelablePromise<Array<ActivityTypeOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/projects/{project_id}/activity-types',
            path: {
                'project_id': projectId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Register Activity Type
     * @returns ActivityTypeOut Successful Response
     * @throws ApiError
     */
    public static registerActivityTypeApiProjectsProjectIdActivityTypesPost({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: ActivityTypeIn,
    }): CancelablePromise<ActivityTypeOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/projects/{project_id}/activity-types',
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
}
