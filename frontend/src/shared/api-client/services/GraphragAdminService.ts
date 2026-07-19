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
        force = false,
    }: {
        configId: string,
        /**
         * Override lock contention and, when 2PC compensation cannot complete, still force idle while recording the incomplete outcome (R11a.02). Accepts data loss: forcing idle also makes the graph readable again, including a partially applied build that can never be rolled back, so those facts re-enter agent context and the graph view. Rebuilding from recovery_unavailable does not require this flag.
         */
        force?: boolean,
    }): CancelablePromise<GraphRagConfigOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/admin/graphrag/{config_id}/reset',
            path: {
                'config_id': configId,
            },
            query: {
                'force': force,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
