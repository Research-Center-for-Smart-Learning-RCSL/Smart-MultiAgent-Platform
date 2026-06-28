/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AllowlistAddIn } from '../models/AllowlistAddIn';
import type { AllowlistEntryOut } from '../models/AllowlistEntryOut';
import type { AllowlistReplaceIn } from '../models/AllowlistReplaceIn';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class McpService {
    /**
     * List Allowlist
     * @returns AllowlistEntryOut Successful Response
     * @throws ApiError
     */
    public static listAllowlistApiProjectsProjectIdMcpEgressAllowlistGet({
        projectId,
    }: {
        projectId: string,
    }): CancelablePromise<Array<AllowlistEntryOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/projects/{project_id}/mcp/egress-allowlist',
            path: {
                'project_id': projectId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Add Allowlist Entry
     * @returns AllowlistEntryOut Successful Response
     * @throws ApiError
     */
    public static addAllowlistEntryApiProjectsProjectIdMcpEgressAllowlistPost({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: AllowlistAddIn,
    }): CancelablePromise<AllowlistEntryOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/projects/{project_id}/mcp/egress-allowlist',
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
     * Replace Allowlist
     * @returns AllowlistEntryOut Successful Response
     * @throws ApiError
     */
    public static replaceAllowlistApiProjectsProjectIdMcpEgressAllowlistPut({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: AllowlistReplaceIn,
    }): CancelablePromise<Array<AllowlistEntryOut>> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/projects/{project_id}/mcp/egress-allowlist',
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
     * Remove Allowlist Entry
     * @returns void
     * @throws ApiError
     */
    public static removeAllowlistEntryApiProjectsProjectIdMcpEgressAllowlistHostnameDelete({
        projectId,
        hostname,
    }: {
        projectId: string,
        hostname: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/projects/{project_id}/mcp/egress-allowlist/{hostname}',
            path: {
                'project_id': projectId,
                'hostname': hostname,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
