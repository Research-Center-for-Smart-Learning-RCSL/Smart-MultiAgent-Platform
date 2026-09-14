/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { app__api__v1__canvas_templates__TemplateCreateIn } from '../models/app__api__v1__canvas_templates__TemplateCreateIn';
import type { app__api__v1__canvas_templates__TemplateOut } from '../models/app__api__v1__canvas_templates__TemplateOut';
import type { TemplateDetailOut } from '../models/TemplateDetailOut';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class CanvasTemplatesService {
    /**
     * List Templates
     * @returns app__api__v1__canvas_templates__TemplateOut Successful Response
     * @throws ApiError
     */
    public static listTemplatesApiCanvasTemplatesGet({
        scope,
        projectId,
    }: {
        scope?: (string | null),
        projectId?: (string | null),
    }): CancelablePromise<Array<app__api__v1__canvas_templates__TemplateOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/canvas-templates',
            query: {
                'scope': scope,
                'project_id': projectId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Create Template
     * @returns app__api__v1__canvas_templates__TemplateOut Successful Response
     * @throws ApiError
     */
    public static createTemplateApiCanvasTemplatesPost({
        requestBody,
    }: {
        requestBody: app__api__v1__canvas_templates__TemplateCreateIn,
    }): CancelablePromise<app__api__v1__canvas_templates__TemplateOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/canvas-templates',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Delete Template
     * @returns void
     * @throws ApiError
     */
    public static deleteTemplateApiCanvasTemplatesTemplateIdDelete({
        templateId,
    }: {
        templateId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/canvas-templates/{template_id}',
            path: {
                'template_id': templateId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Get Template
     * @returns TemplateDetailOut Successful Response
     * @throws ApiError
     */
    public static getTemplateApiCanvasTemplatesTemplateIdGet({
        templateId,
    }: {
        templateId: string,
    }): CancelablePromise<TemplateDetailOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/canvas-templates/{template_id}',
            path: {
                'template_id': templateId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
}
