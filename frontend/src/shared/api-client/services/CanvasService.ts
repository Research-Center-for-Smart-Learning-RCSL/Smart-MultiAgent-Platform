/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { app__api__v1__canvas_templates__TemplateOut } from '../models/app__api__v1__canvas_templates__TemplateOut';
import type { ApplyTemplateIn } from '../models/ApplyTemplateIn';
import type { BatchOpIn } from '../models/BatchOpIn';
import type { Body_upload_image_api_chatrooms__chatroom_id__canvas_images_post } from '../models/Body_upload_image_api_chatrooms__chatroom_id__canvas_images_post';
import type { CanvasObjectIn } from '../models/CanvasObjectIn';
import type { CanvasObjectOut } from '../models/CanvasObjectOut';
import type { CanvasObjectPatch } from '../models/CanvasObjectPatch';
import type { CanvasOut } from '../models/CanvasOut';
import type { CanvasSearchResult } from '../models/CanvasSearchResult';
import type { CanvasSettingsIn } from '../models/CanvasSettingsIn';
import type { CommentIn } from '../models/CommentIn';
import type { CommentOut } from '../models/CommentOut';
import type { SaveAsTemplateIn } from '../models/SaveAsTemplateIn';
import type { SnapshotCreateIn } from '../models/SnapshotCreateIn';
import type { SnapshotDetailOut } from '../models/SnapshotDetailOut';
import type { SnapshotOut } from '../models/SnapshotOut';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class CanvasService {
    /**
     * Get Canvas
     * @returns CanvasOut Successful Response
     * @throws ApiError
     */
    public static getCanvasApiChatroomsChatroomIdCanvasGet({
        chatroomId,
    }: {
        chatroomId: string,
    }): CancelablePromise<CanvasOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/chatrooms/{chatroom_id}/canvas',
            path: {
                'chatroom_id': chatroomId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Update Canvas Settings
     * @returns CanvasOut Successful Response
     * @throws ApiError
     */
    public static updateCanvasSettingsApiChatroomsChatroomIdCanvasPatch({
        chatroomId,
        requestBody,
    }: {
        chatroomId: string,
        requestBody: CanvasSettingsIn,
    }): CancelablePromise<CanvasOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/chatrooms/{chatroom_id}/canvas',
            path: {
                'chatroom_id': chatroomId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Delete Canvas
     * @returns void
     * @throws ApiError
     */
    public static deleteCanvasApiChatroomsChatroomIdCanvasDelete({
        chatroomId,
    }: {
        chatroomId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/chatrooms/{chatroom_id}/canvas',
            path: {
                'chatroom_id': chatroomId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Search Canvas
     * @returns CanvasSearchResult Successful Response
     * @throws ApiError
     */
    public static searchCanvasApiChatroomsChatroomIdCanvasSearchGet({
        chatroomId,
        q,
        limit = 50,
    }: {
        chatroomId: string,
        q: string,
        limit?: number,
    }): CancelablePromise<Array<CanvasSearchResult>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/chatrooms/{chatroom_id}/canvas/search',
            path: {
                'chatroom_id': chatroomId,
            },
            query: {
                'q': q,
                'limit': limit,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * List Objects
     * @returns CanvasObjectOut Successful Response
     * @throws ApiError
     */
    public static listObjectsApiChatroomsChatroomIdCanvasObjectsGet({
        chatroomId,
        limit = 500,
        offset,
    }: {
        chatroomId: string,
        limit?: number,
        offset?: number,
    }): CancelablePromise<Array<CanvasObjectOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/chatrooms/{chatroom_id}/canvas/objects',
            path: {
                'chatroom_id': chatroomId,
            },
            query: {
                'limit': limit,
                'offset': offset,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Create Object
     * @returns CanvasObjectOut Successful Response
     * @throws ApiError
     */
    public static createObjectApiChatroomsChatroomIdCanvasObjectsPost({
        chatroomId,
        requestBody,
    }: {
        chatroomId: string,
        requestBody: CanvasObjectIn,
    }): CancelablePromise<CanvasObjectOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/chatrooms/{chatroom_id}/canvas/objects',
            path: {
                'chatroom_id': chatroomId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Update Object
     * @returns CanvasObjectOut Successful Response
     * @throws ApiError
     */
    public static updateObjectApiChatroomsChatroomIdCanvasObjectsObjectIdPatch({
        chatroomId,
        objectId,
        requestBody,
    }: {
        chatroomId: string,
        objectId: string,
        requestBody: CanvasObjectPatch,
    }): CancelablePromise<CanvasObjectOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/chatrooms/{chatroom_id}/canvas/objects/{object_id}',
            path: {
                'chatroom_id': chatroomId,
                'object_id': objectId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Delete Object
     * @returns void
     * @throws ApiError
     */
    public static deleteObjectApiChatroomsChatroomIdCanvasObjectsObjectIdDelete({
        chatroomId,
        objectId,
    }: {
        chatroomId: string,
        objectId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/chatrooms/{chatroom_id}/canvas/objects/{object_id}',
            path: {
                'chatroom_id': chatroomId,
                'object_id': objectId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Batch Operate
     * @returns any Successful Response
     * @throws ApiError
     */
    public static batchOperateApiChatroomsChatroomIdCanvasObjectsBatchPost({
        chatroomId,
        requestBody,
    }: {
        chatroomId: string,
        requestBody: BatchOpIn,
    }): CancelablePromise<Record<string, any>> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/chatrooms/{chatroom_id}/canvas/objects/batch',
            path: {
                'chatroom_id': chatroomId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Upload Image
     * @returns CanvasObjectOut Successful Response
     * @throws ApiError
     */
    public static uploadImageApiChatroomsChatroomIdCanvasImagesPost({
        chatroomId,
        formData,
    }: {
        chatroomId: string,
        formData: Body_upload_image_api_chatrooms__chatroom_id__canvas_images_post,
    }): CancelablePromise<CanvasObjectOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/chatrooms/{chatroom_id}/canvas/images',
            path: {
                'chatroom_id': chatroomId,
            },
            formData: formData,
            mediaType: 'multipart/form-data',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * List Snapshots
     * @returns SnapshotOut Successful Response
     * @throws ApiError
     */
    public static listSnapshotsApiChatroomsChatroomIdCanvasSnapshotsGet({
        chatroomId,
        limit = 20,
        offset,
    }: {
        chatroomId: string,
        limit?: number,
        offset?: number,
    }): CancelablePromise<Array<SnapshotOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/chatrooms/{chatroom_id}/canvas/snapshots',
            path: {
                'chatroom_id': chatroomId,
            },
            query: {
                'limit': limit,
                'offset': offset,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Create Snapshot
     * @returns SnapshotOut Successful Response
     * @throws ApiError
     */
    public static createSnapshotApiChatroomsChatroomIdCanvasSnapshotsPost({
        chatroomId,
        requestBody,
    }: {
        chatroomId: string,
        requestBody?: (SnapshotCreateIn | null),
    }): CancelablePromise<SnapshotOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/chatrooms/{chatroom_id}/canvas/snapshots',
            path: {
                'chatroom_id': chatroomId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Get Snapshot
     * @returns SnapshotDetailOut Successful Response
     * @throws ApiError
     */
    public static getSnapshotApiChatroomsChatroomIdCanvasSnapshotsSnapshotIdGet({
        chatroomId,
        snapshotId,
    }: {
        chatroomId: string,
        snapshotId: string,
    }): CancelablePromise<SnapshotDetailOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/chatrooms/{chatroom_id}/canvas/snapshots/{snapshot_id}',
            path: {
                'chatroom_id': chatroomId,
                'snapshot_id': snapshotId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Restore Snapshot
     * @returns SnapshotOut Successful Response
     * @throws ApiError
     */
    public static restoreSnapshotApiChatroomsChatroomIdCanvasSnapshotsSnapshotIdRestorePost({
        chatroomId,
        snapshotId,
    }: {
        chatroomId: string,
        snapshotId: string,
    }): CancelablePromise<SnapshotOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/chatrooms/{chatroom_id}/canvas/snapshots/{snapshot_id}/restore',
            path: {
                'chatroom_id': chatroomId,
                'snapshot_id': snapshotId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Get Comment Counts
     * @returns number Successful Response
     * @throws ApiError
     */
    public static getCommentCountsApiChatroomsChatroomIdCanvasObjectsCommentCountsGet({
        chatroomId,
    }: {
        chatroomId: string,
    }): CancelablePromise<Record<string, number>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/chatrooms/{chatroom_id}/canvas/objects/comment-counts',
            path: {
                'chatroom_id': chatroomId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * List Comments
     * @returns CommentOut Successful Response
     * @throws ApiError
     */
    public static listCommentsApiChatroomsChatroomIdCanvasObjectsObjectIdCommentsGet({
        chatroomId,
        objectId,
        limit = 50,
        offset,
    }: {
        chatroomId: string,
        objectId: string,
        limit?: number,
        offset?: number,
    }): CancelablePromise<Array<CommentOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/chatrooms/{chatroom_id}/canvas/objects/{object_id}/comments',
            path: {
                'chatroom_id': chatroomId,
                'object_id': objectId,
            },
            query: {
                'limit': limit,
                'offset': offset,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Create Comment
     * @returns CommentOut Successful Response
     * @throws ApiError
     */
    public static createCommentApiChatroomsChatroomIdCanvasObjectsObjectIdCommentsPost({
        chatroomId,
        objectId,
        requestBody,
    }: {
        chatroomId: string,
        objectId: string,
        requestBody: CommentIn,
    }): CancelablePromise<CommentOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/chatrooms/{chatroom_id}/canvas/objects/{object_id}/comments',
            path: {
                'chatroom_id': chatroomId,
                'object_id': objectId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Update Comment
     * @returns CommentOut Successful Response
     * @throws ApiError
     */
    public static updateCommentApiChatroomsChatroomIdCanvasCommentsCommentIdPatch({
        chatroomId,
        commentId,
        requestBody,
    }: {
        chatroomId: string,
        commentId: string,
        requestBody: CommentIn,
    }): CancelablePromise<CommentOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/chatrooms/{chatroom_id}/canvas/comments/{comment_id}',
            path: {
                'chatroom_id': chatroomId,
                'comment_id': commentId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Delete Comment
     * @returns void
     * @throws ApiError
     */
    public static deleteCommentApiChatroomsChatroomIdCanvasCommentsCommentIdDelete({
        chatroomId,
        commentId,
    }: {
        chatroomId: string,
        commentId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/chatrooms/{chatroom_id}/canvas/comments/{comment_id}',
            path: {
                'chatroom_id': chatroomId,
                'comment_id': commentId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Apply Template
     * @returns any Successful Response
     * @throws ApiError
     */
    public static applyTemplateApiChatroomsChatroomIdCanvasApplyTemplatePost({
        chatroomId,
        requestBody,
    }: {
        chatroomId: string,
        requestBody: ApplyTemplateIn,
    }): CancelablePromise<Record<string, any>> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/chatrooms/{chatroom_id}/canvas/apply-template',
            path: {
                'chatroom_id': chatroomId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Save As Template
     * @returns app__api__v1__canvas_templates__TemplateOut Successful Response
     * @throws ApiError
     */
    public static saveAsTemplateApiChatroomsChatroomIdCanvasSaveAsTemplatePost({
        chatroomId,
        requestBody,
    }: {
        chatroomId: string,
        requestBody: SaveAsTemplateIn,
    }): CancelablePromise<app__api__v1__canvas_templates__TemplateOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/chatrooms/{chatroom_id}/canvas/save-as-template',
            path: {
                'chatroom_id': chatroomId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
}
