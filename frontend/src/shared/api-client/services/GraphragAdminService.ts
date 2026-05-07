/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { GraphRagConfigOut } from '../models/GraphRagConfigOut';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class GraphragAdminService {
    /**
     * Admin Reset
     * @returns GraphRagConfigOut Successful Response
     * @throws ApiError
     */
    public static adminResetApiAdminGraphragConfigIdResetPost({
        configId,
    }: {
        configId: string,
    }): CancelablePromise<GraphRagConfigOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/admin/graphrag/{config_id}/reset',
            path: {
                'config_id': configId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
