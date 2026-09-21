/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ResearchExportCreateIn } from '../models/ResearchExportCreateIn';
import type { ResearchExportCreateOut } from '../models/ResearchExportCreateOut';
import type { ResearchExportStatusOut } from '../models/ResearchExportStatusOut';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ResearchExportService {
    /**
     * Create Research Export
     * @returns ResearchExportCreateOut Successful Response
     * @throws ApiError
     */
    public static createResearchExportApiWorkspacesWorkspaceIdExportResearchDataPost({
        workspaceId,
        requestBody,
    }: {
        workspaceId: string,
        requestBody?: (ResearchExportCreateIn | null),
    }): CancelablePromise<ResearchExportCreateOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/workspaces/{workspace_id}/export/research-data',
            path: {
                'workspace_id': workspaceId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Get Research Export
     * @returns ResearchExportStatusOut Successful Response
     * @throws ApiError
     */
    public static getResearchExportApiExportsResearchJobIdGet({
        jobId,
    }: {
        jobId: string,
    }): CancelablePromise<ResearchExportStatusOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/exports/research/{job_id}',
            path: {
                'job_id': jobId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
}
