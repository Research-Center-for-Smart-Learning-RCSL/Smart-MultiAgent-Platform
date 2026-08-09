/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ActivityActivationOut } from '../models/ActivityActivationOut';
import type { ActivityActivationStartIn } from '../models/ActivityActivationStartIn';
import type { ActivityPolicyPublicOut } from '../models/ActivityPolicyPublicOut';
import type { ActivitySessionOpenIn } from '../models/ActivitySessionOpenIn';
import type { ActivitySessionOut } from '../models/ActivitySessionOut';
import type { ActivitySubmissionIn } from '../models/ActivitySubmissionIn';
import type { ActivitySubmissionOut } from '../models/ActivitySubmissionOut';
import type { ActivitySubmissionsPageOut } from '../models/ActivitySubmissionsPageOut';
import type { ActivityTypeIn } from '../models/ActivityTypeIn';
import type { ActivityTypeOut } from '../models/ActivityTypeOut';
import type { ActivityTypePublicOut } from '../models/ActivityTypePublicOut';
import type { ActivityTypeUpdateIn } from '../models/ActivityTypeUpdateIn';
import type { ActivityValidatorOut } from '../models/ActivityValidatorOut';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ActivitiesService {
    /**
     * Get Activity Policy Public
     * The policy in force, for the authoring form. Permissive when none is saved.
     * @returns ActivityPolicyPublicOut Successful Response
     * @throws ApiError
     */
    public static getActivityPolicyPublicApiActivityPolicyGet(): CancelablePromise<ActivityPolicyPublicOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/activity-policy',
        });
    }
    /**
     * List Activity Validators
     * List the registered first-party in-process validators (R30.24). Global and
     * process-scoped — availability never varies per project — so any authenticated
     * caller reads the same set the picker draws from. Exposes ids/titles only.
     * @returns ActivityValidatorOut Successful Response
     * @throws ApiError
     */
    public static listActivityValidatorsApiActivityValidatorsGet(): CancelablePromise<Array<ActivityValidatorOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/activity-validators',
        });
    }
    /**
     * Start Activity Activation
     * @returns ActivityActivationOut Successful Response
     * @throws ApiError
     */
    public static startActivityActivationApiChatroomsChatroomIdActivityActivationsPost({
        chatroomId,
        requestBody,
    }: {
        chatroomId: string,
        requestBody: ActivityActivationStartIn,
    }): CancelablePromise<ActivityActivationOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/chatrooms/{chatroom_id}/activity-activations',
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
     * Get Active Activity Activation
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getActiveActivityActivationApiChatroomsChatroomIdActivityActivationsActiveGet({
        chatroomId,
    }: {
        chatroomId: string,
    }): CancelablePromise<(ActivityActivationOut | null)> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/chatrooms/{chatroom_id}/activity-activations/active',
            path: {
                'chatroom_id': chatroomId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * End Activity Activation
     * @returns ActivityActivationOut Successful Response
     * @throws ApiError
     */
    public static endActivityActivationApiChatroomsChatroomIdActivityActivationsActivationIdEndPatch({
        chatroomId,
        activationId,
    }: {
        chatroomId: string,
        activationId: string,
    }): CancelablePromise<ActivityActivationOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/chatrooms/{chatroom_id}/activity-activations/{activation_id}/end',
            path: {
                'chatroom_id': chatroomId,
                'activation_id': activationId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
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
     * Get Room Activity Type
     * Room-scoped rendering-contract read (R30.26, Q-1): the recovery path
     * when the activation-started broadcast was missed, the store was reset, or
     * a future flow needs a type that is not the currently active one. Gated by
     * the room-access chain, not project membership, so a chatroom guest is a
     * full activity participant.
     * @returns ActivityTypePublicOut Successful Response
     * @throws ApiError
     */
    public static getRoomActivityTypeApiChatroomsChatroomIdActivityTypesTypeIdGet({
        chatroomId,
        typeId,
    }: {
        chatroomId: string,
        typeId: string,
    }): CancelablePromise<ActivityTypePublicOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/chatrooms/{chatroom_id}/activity-types/{type_id}',
            path: {
                'chatroom_id': chatroomId,
                'type_id': typeId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Activity Types
     * Membership gate is unchanged — a non-owner member still legitimately
     * lists types (that is how a facilitator picks one to activate). Only
     * `validator_config` is owner-gated (R30.25): it may hold answer keys and,
     * once sealed validator credentials exist, secrets.
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
    /**
     * Delete Activity Type
     * @returns void
     * @throws ApiError
     */
    public static deleteActivityTypeApiProjectsProjectIdActivityTypesTypeIdDelete({
        projectId,
        typeId,
    }: {
        projectId: string,
        typeId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/projects/{project_id}/activity-types/{type_id}',
            path: {
                'project_id': projectId,
                'type_id': typeId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update Activity Type
     * @returns ActivityTypeOut Successful Response
     * @throws ApiError
     */
    public static updateActivityTypeApiProjectsProjectIdActivityTypesTypeIdPatch({
        projectId,
        typeId,
        requestBody,
    }: {
        projectId: string,
        typeId: string,
        requestBody: ActivityTypeUpdateIn,
    }): CancelablePromise<ActivityTypeOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/projects/{project_id}/activity-types/{type_id}',
            path: {
                'project_id': projectId,
                'type_id': typeId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
