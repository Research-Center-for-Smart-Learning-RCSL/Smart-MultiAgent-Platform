/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ExportCreateIn } from '../models/ExportCreateIn';
import type { ExportCreateOut } from '../models/ExportCreateOut';
import type { ExportStatusOut } from '../models/ExportStatusOut';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ExportsService {
    /**
     * Create Export
     * @returns ExportCreateOut Successful Response
     * @throws ApiError
     */
    public static createExportApiChatroomsChatroomIdExportPost({
        chatroomId,
        requestBody,
    }: {
        chatroomId: string,
        requestBody?: (ExportCreateIn | null),
    }): CancelablePromise<ExportCreateOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/chatrooms/{chatroom_id}/export',
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
     * Get Export
     * @returns ExportStatusOut Successful Response
     * @throws ApiError
     */
    public static getExportApiExportsJobIdGet({
        jobId,
    }: {
        jobId: string,
    }): CancelablePromise<ExportStatusOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/exports/{job_id}',
            path: {
                'job_id': jobId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
