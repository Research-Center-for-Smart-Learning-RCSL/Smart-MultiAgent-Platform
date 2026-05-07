/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AttachmentDownloadOut } from '../models/AttachmentDownloadOut';
import type { AttachmentOut } from '../models/AttachmentOut';
import type { Body_create_single_shot_api_chatrooms__chatroom_id__attachments_post } from '../models/Body_create_single_shot_api_chatrooms__chatroom_id__attachments_post';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class AttachmentsService {
    /**
     * Read Attachment
     * @returns AttachmentDownloadOut Successful Response
     * @throws ApiError
     */
    public static readAttachmentApiAttachmentsAttachmentIdGet({
        attachmentId,
    }: {
        attachmentId: string,
    }): CancelablePromise<AttachmentDownloadOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/attachments/{attachment_id}',
            path: {
                'attachment_id': attachmentId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Single Shot
     * @returns AttachmentOut Successful Response
     * @throws ApiError
     */
    public static createSingleShotApiChatroomsChatroomIdAttachmentsPost({
        chatroomId,
        formData,
    }: {
        chatroomId: string,
        formData: Body_create_single_shot_api_chatrooms__chatroom_id__attachments_post,
    }): CancelablePromise<AttachmentOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/chatrooms/{chatroom_id}/attachments',
            path: {
                'chatroom_id': chatroomId,
            },
            formData: formData,
            mediaType: 'multipart/form-data',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
