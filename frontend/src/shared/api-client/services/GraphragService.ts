/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { GraphRagBuildOut } from '../models/GraphRagBuildOut';
import type { GraphRagConfigCreateIn } from '../models/GraphRagConfigCreateIn';
import type { GraphRagConfigOut } from '../models/GraphRagConfigOut';
import type { GraphRagConfigPatchIn } from '../models/GraphRagConfigPatchIn';
import type { GraphRagStatusOut } from '../models/GraphRagStatusOut';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class GraphragService {
    /**
     * Delete Config
     * @returns void
     * @throws ApiError
     */
    public static deleteConfigApiGraphragConfigIdDelete({
        configId,
    }: {
        configId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/graphrag/{config_id}',
            path: {
                'config_id': configId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Read Config
     * @returns GraphRagConfigOut Successful Response
     * @throws ApiError
     */
    public static readConfigApiGraphragConfigIdGet({
        configId,
    }: {
        configId: string,
    }): CancelablePromise<GraphRagConfigOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/graphrag/{config_id}',
            path: {
                'config_id': configId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update Config
     * R11.05 — edit trigger config or builder key group.
     *
     * AuthZ matches DELETE: ``RESOURCE_CREATE_EDIT`` at the config's project.
     * @returns GraphRagConfigOut Successful Response
     * @throws ApiError
     */
    public static updateConfigApiGraphragConfigIdPatch({
        configId,
        requestBody,
    }: {
        configId: string,
        requestBody: GraphRagConfigPatchIn,
    }): CancelablePromise<GraphRagConfigOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/graphrag/{config_id}',
            path: {
                'config_id': configId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Trigger Build
     * @returns GraphRagBuildOut Successful Response
     * @throws ApiError
     */
    public static triggerBuildApiGraphragConfigIdBuildPost({
        configId,
    }: {
        configId: string,
    }): CancelablePromise<GraphRagBuildOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/graphrag/{config_id}/build',
            path: {
                'config_id': configId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Read Status
     * @returns GraphRagStatusOut Successful Response
     * @throws ApiError
     */
    public static readStatusApiGraphragConfigIdStatusGet({
        configId,
    }: {
        configId: string,
    }): CancelablePromise<GraphRagStatusOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/graphrag/{config_id}/status',
            path: {
                'config_id': configId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Configs
     * @returns GraphRagConfigOut Successful Response
     * @throws ApiError
     */
    public static listConfigsApiProjectsProjectIdGraphragConfigsGet({
        projectId,
    }: {
        projectId: string,
    }): CancelablePromise<Array<GraphRagConfigOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/projects/{project_id}/graphrag-configs',
            path: {
                'project_id': projectId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Config
     * @returns GraphRagConfigOut Successful Response
     * @throws ApiError
     */
    public static createConfigApiProjectsProjectIdGraphragConfigsPost({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: GraphRagConfigCreateIn,
    }): CancelablePromise<GraphRagConfigOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/projects/{project_id}/graphrag-configs',
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
}
