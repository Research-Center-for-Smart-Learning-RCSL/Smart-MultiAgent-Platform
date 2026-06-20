/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Body_upload_document_api_rag_configs__config_id__documents_post } from '../models/Body_upload_document_api_rag_configs__config_id__documents_post';
import type { RagConfigCreateIn } from '../models/RagConfigCreateIn';
import type { RagConfigOut } from '../models/RagConfigOut';
import type { RagConfigPatchIn } from '../models/RagConfigPatchIn';
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
        limit = 100,
        offset,
    }: {
        projectId: string,
        /**
         * Max items to return
         */
        limit?: number,
        /**
         * Number of items to skip
         */
        offset?: number,
    }): CancelablePromise<Array<RagConfigOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/projects/{project_id}/rag-configs',
            path: {
                'project_id': projectId,
            },
            query: {
                'limit': limit,
                'offset': offset,
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
     * §22.7 — soft-delete a RAG config and cascade its children.
     *
     * DOM-1: a config's child documents/chunks/vectors/blobs are not removed
     * by the soft delete on its own row. ``RagConfigService.soft_delete``
     * hard-deletes the document rows (``rag_chunks`` cascade via FK); this
     * endpoint then commits and purges the Qdrant points + MinIO blobs
     * best-effort. Ordering matters (DOM-4): the commit is the point of no
     * return, so the destructive infra step always trails a durable audit row.
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
     * Patch Rag Config
     * @returns RagConfigOut Successful Response
     * @throws ApiError
     */
    public static patchRagConfigApiRagConfigsConfigIdPatch({
        configId,
        requestBody,
    }: {
        configId: string,
        requestBody: RagConfigPatchIn,
    }): CancelablePromise<RagConfigOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/rag-configs/{config_id}',
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
     * List Documents
     * @returns RagDocumentOut Successful Response
     * @throws ApiError
     */
    public static listDocumentsApiRagConfigsConfigIdDocumentsGet({
        configId,
        limit = 100,
        offset,
    }: {
        configId: string,
        /**
         * Max items to return
         */
        limit?: number,
        /**
         * Number of items to skip
         */
        offset?: number,
    }): CancelablePromise<Array<RagDocumentOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/rag-configs/{config_id}/documents',
            path: {
                'config_id': configId,
            },
            query: {
                'limit': limit,
                'offset': offset,
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
     * Removes the row + ``rag_chunks`` (FK cascade), then cleans up the Qdrant
     * points and MinIO blob best-effort. Ordering matters (DOM-4): the DB row
     * and audit record are written and committed *first* — the previous code
     * deleted the Qdrant points and blob before the row/audit, so a rollback
     * could leave an undeletable tombstone whose vectors+blob were already
     * gone, with no audit row of the destructive action.
     *
     * AuthZ matches upload: ``RESOURCE_CREATE_EDIT`` at the parent config's
     * project. We do NOT require Project Owner here — the R10.10 owner gate
     * covers ingestion (write) only; deletion follows the standard edit
     * capability so non-owner editors can clean up their own uploads.
     *
     * DOM-7: a missing document and an existing-but-forbidden document both
     * return 404. Branching on existence before authorization (the old code
     * returned 204 for any missing id) leaked a cross-tenant UUID enumeration
     * oracle; deletion is therefore no longer idempotent for a vanished id.
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
