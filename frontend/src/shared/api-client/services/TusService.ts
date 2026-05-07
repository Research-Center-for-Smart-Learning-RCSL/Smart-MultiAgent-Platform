/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class TusService {
    /**
     * Tus Options
     * @returns any Successful Response
     * @throws ApiError
     */
    public static tusOptionsApiTusOptions(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'OPTIONS',
            url: '/api/tus',
        });
    }
    /**
     * Tus Create
     * @returns any Successful Response
     * @throws ApiError
     */
    public static tusCreateApiTusPost({
        tusResumable,
        uploadLength,
        uploadMetadata = '',
    }: {
        tusResumable: string,
        uploadLength: number,
        uploadMetadata?: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/tus',
            headers: {
                'Tus-Resumable': tusResumable,
                'Upload-Length': uploadLength,
                'Upload-Metadata': uploadMetadata,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Tus Terminate
     * @returns void
     * @throws ApiError
     */
    public static tusTerminateApiTusUploadIdDelete({
        uploadId,
        tusResumable,
    }: {
        uploadId: string,
        tusResumable: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/tus/{upload_id}',
            path: {
                'upload_id': uploadId,
            },
            headers: {
                'Tus-Resumable': tusResumable,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Tus Head
     * @returns any Successful Response
     * @throws ApiError
     */
    public static tusHeadApiTusUploadIdHead({
        uploadId,
        tusResumable,
    }: {
        uploadId: string,
        tusResumable: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'HEAD',
            url: '/api/tus/{upload_id}',
            path: {
                'upload_id': uploadId,
            },
            headers: {
                'Tus-Resumable': tusResumable,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Tus Patch
     * @returns any Successful Response
     * @throws ApiError
     */
    public static tusPatchApiTusUploadIdPatch({
        uploadId,
        tusResumable,
        uploadOffset,
        contentType,
    }: {
        uploadId: string,
        tusResumable: string,
        uploadOffset: number,
        contentType: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/tus/{upload_id}',
            path: {
                'upload_id': uploadId,
            },
            headers: {
                'Tus-Resumable': tusResumable,
                'Upload-Offset': uploadOffset,
                'Content-Type': contentType,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
