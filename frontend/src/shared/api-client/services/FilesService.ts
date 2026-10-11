/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class FilesService {
    /**
     * Get File
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getFileApiFilesTicketGet({
        ticket,
    }: {
        ticket: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/files/{ticket}',
            path: {
                'ticket': ticket,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Head File
     * @returns any Successful Response
     * @throws ApiError
     */
    public static headFileApiFilesTicketHead({
        ticket,
    }: {
        ticket: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'HEAD',
            url: '/api/files/{ticket}',
            path: {
                'ticket': ticket,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
}
