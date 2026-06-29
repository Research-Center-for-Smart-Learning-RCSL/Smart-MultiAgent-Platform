/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ModelCatalogOut } from '../models/ModelCatalogOut';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ModelCatalogService {
    /**
     * Get Model Catalog
     * @returns ModelCatalogOut Successful Response
     * @throws ApiError
     */
    public static getModelCatalogApiModelCatalogGet(): CancelablePromise<ModelCatalogOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/model-catalog',
        });
    }
}
