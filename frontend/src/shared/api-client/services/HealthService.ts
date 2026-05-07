/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class HealthService {
    /**
     * Healthz
     * @returns string Successful Response
     * @throws ApiError
     */
    public static healthzHealthzGet(): CancelablePromise<Record<string, string>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/healthz',
        });
    }
    /**
     * Readyz
     * @returns any Successful Response
     * @throws ApiError
     */
    public static readyzReadyzGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/readyz',
        });
    }
}
