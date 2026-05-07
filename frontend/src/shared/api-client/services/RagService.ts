/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Body_upload_document_api_rag_configs__config_id__documents_post } from '../models/Body_upload_document_api_rag_configs__config_id__documents_post';
import type { RagConfigCreateIn } from '../models/RagConfigCreateIn';
import type { RagConfigOut } from '../models/RagConfigOut';
import type { RagDocumentOut } from '../models/RagDocumentOut';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class RagService {
    /**
     * List Rag Configs
     * @returns RagConfigOut Successful Response
     * @throws ApiError
     */
    public static listRagConfigsApiProjectsProjectIdRagConfigsGet({
        projectId,
    }: {
        projectId: string,
    }): CancelablePromise<Array<RagConfigOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/projects/{project_id}/rag-configs',
            path: {
                'project_id': projectId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Rag Config
     * @returns RagConfigOut Successful Response
     * @throws ApiError
     */
    public static createRagConfigApiProjectsProjectIdRagConfigsPost({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: RagConfigCreateIn,
    }): CancelablePromise<RagConfigOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/projects/{project_id}/rag-configs',
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
     * Delete Rag Config
     * @returns void
     * @throws ApiError
     */
    public static deleteRagConfigApiRagConfigsConfigIdDelete({
        configId,
    }: {
        configId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/rag-configs/{config_id}',
            path: {
                'config_id': configId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Read Rag Config
     * @returns RagConfigOut Successful Response
     * @throws ApiError
     */
    public static readRagConfigApiRagConfigsConfigIdGet({
        configId,
    }: {
        configId: string,
    }): CancelablePromise<RagConfigOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/rag-configs/{config_id}',
            path: {
                'config_id': configId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Documents
     * @returns RagDocumentOut Successful Response
     * @throws ApiError
     */
    public static listDocumentsApiRagConfigsConfigIdDocumentsGet({
        configId,
    }: {
        configId: string,
    }): CancelablePromise<Array<RagDocumentOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/rag-configs/{config_id}/documents',
            path: {
                'config_id': configId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Upload Document
     * @returns RagDocumentOut Successful Response
     * @throws ApiError
     */
    public static uploadDocumentApiRagConfigsConfigIdDocumentsPost({
        configId,
        formData,
    }: {
        configId: string,
        formData: Body_upload_document_api_rag_configs__config_id__documents_post,
    }): CancelablePromise<RagDocumentOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/rag-configs/{config_id}/documents',
            path: {
                'config_id': configId,
            },
            formData: formData,
            mediaType: 'multipart/form-data',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Rag Document
     * §22.7 — hard-delete a RAG document.
     *
     * Cleans up Qdrant points and the MinIO blob best-effort. The chunk rows
     * are removed by the FK cascade on ``rag_chunks.document_id``.
     *
     * AuthZ matches upload: ``RESOURCE_CREATE_EDIT`` at the parent config's
     * project. We do NOT require Project Owner here — the R10.10 owner gate
     * covers ingestion (write) only; deletion follows the standard edit
     * capability so non-owner editors can clean up their own uploads.
     * @returns void
     * @throws ApiError
     */
    public static deleteRagDocumentApiRagDocumentsDocumentIdDelete({
        documentId,
    }: {
        documentId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/rag-documents/{document_id}',
            path: {
                'document_id': documentId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
