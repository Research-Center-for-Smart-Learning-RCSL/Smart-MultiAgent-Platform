/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Body_upload_knowmap_document_api_knowmap_configs__config_id__documents_post } from '../models/Body_upload_knowmap_document_api_knowmap_configs__config_id__documents_post';
import type { KnowmapConfigCreateIn } from '../models/KnowmapConfigCreateIn';
import type { KnowmapConfigOut } from '../models/KnowmapConfigOut';
import type { KnowmapConfigPatchIn } from '../models/KnowmapConfigPatchIn';
import type { KnowmapDocumentAgentsPatchIn } from '../models/KnowmapDocumentAgentsPatchIn';
import type { KnowmapDocumentOut } from '../models/KnowmapDocumentOut';
import type { KnowmapGraphOut } from '../models/KnowmapGraphOut';
import type { KnowmapRebuildAck } from '../models/KnowmapRebuildAck';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class KnowmapService {
    /**
     * Delete Knowmap Config
     * Soft-delete a Knowledge Map config and cascade its children (R11.20).
     *
     * DOM-4: the DB soft-delete + child-document removal + audit are committed
     * first (point of no return); only then are the irreversible external stores
     * (Neo4j subgraph, knowmap Qdrant points, MinIO blobs) purged best-effort.
     * @returns void
     * @throws ApiError
     */
    public static deleteKnowmapConfigApiKnowmapConfigsConfigIdDelete({
        configId,
    }: {
        configId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/knowmap-configs/{config_id}',
            path: {
                'config_id': configId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Read Knowmap Config
     * @returns KnowmapConfigOut Successful Response
     * @throws ApiError
     */
    public static readKnowmapConfigApiKnowmapConfigsConfigIdGet({
        configId,
    }: {
        configId: string,
    }): CancelablePromise<KnowmapConfigOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/knowmap-configs/{config_id}',
            path: {
                'config_id': configId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Patch Knowmap Config
     * @returns KnowmapConfigOut Successful Response
     * @throws ApiError
     */
    public static patchKnowmapConfigApiKnowmapConfigsConfigIdPatch({
        configId,
        requestBody,
    }: {
        configId: string,
        requestBody: KnowmapConfigPatchIn,
    }): CancelablePromise<KnowmapConfigOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/knowmap-configs/{config_id}',
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
     * List Knowmap Documents
     * @returns KnowmapDocumentOut Successful Response
     * @throws ApiError
     */
    public static listKnowmapDocumentsApiKnowmapConfigsConfigIdDocumentsGet({
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
    }): CancelablePromise<Array<KnowmapDocumentOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/knowmap-configs/{config_id}/documents',
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
     * Upload Knowmap Document
     * @returns KnowmapDocumentOut Successful Response
     * @throws ApiError
     */
    public static uploadKnowmapDocumentApiKnowmapConfigsConfigIdDocumentsPost({
        configId,
        formData,
    }: {
        configId: string,
        formData: Body_upload_knowmap_document_api_knowmap_configs__config_id__documents_post,
    }): CancelablePromise<KnowmapDocumentOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/knowmap-configs/{config_id}/documents',
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
     * Read Knowmap Graph
     * Read a bounded view of the config's knowledge graph (Phase 3β, R11.24).
     *
     * AuthZ matches ``read_knowmap_config``: membership at the config's project.
     * The limit is clamped so a large graph can never stream an unbounded
     * payload to the browser — mirrors ``graphrag.py``'s ``read_graph``, which
     * this route now matches structurally too: through ``KnowledgeFacade``
     * rather than instantiating application-layer services directly (found in
     * code review — this file's other routes still bypass the facade, a
     * pre-existing pattern this one deliberately does not extend further).
     * @returns KnowmapGraphOut Successful Response
     * @throws ApiError
     */
    public static readKnowmapGraphApiKnowmapConfigsConfigIdGraphGet({
        configId,
        limit = 500,
    }: {
        configId: string,
        limit?: number,
    }): CancelablePromise<KnowmapGraphOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/knowmap-configs/{config_id}/graph',
            path: {
                'config_id': configId,
            },
            query: {
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Rebuild Knowmap Config
     * Explicit designer rebuild (Q-3/AC-6). Enqueues a ``knowmap_build`` with the
     * dedup job id so a redundant click collapses onto an in-flight build.
     * @returns KnowmapRebuildAck Successful Response
     * @throws ApiError
     */
    public static rebuildKnowmapConfigApiKnowmapConfigsConfigIdRebuildPost({
        configId,
    }: {
        configId: string,
    }): CancelablePromise<KnowmapRebuildAck> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/knowmap-configs/{config_id}/rebuild',
            path: {
                'config_id': configId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Knowmap Document
     * Hard-delete a Knowledge Map document, then purge its MinIO blob and enqueue
     * a rebuild so the graph drops the document's triples (R11.20 / AC-7).
     *
     * DOM-7: a missing document and an existing-but-forbidden one both return 404 so
     * the endpoint is not a cross-tenant UUID oracle. DOM-4: the row + audit commit
     * before the irreversible blob purge.
     * @returns void
     * @throws ApiError
     */
    public static deleteKnowmapDocumentApiKnowmapDocumentsDocumentIdDelete({
        documentId,
    }: {
        documentId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/knowmap-documents/{document_id}',
            path: {
                'document_id': documentId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Set Knowmap Document Agents
     * Replace a document's per-agent allowlist. Owner-gated (R10.10 analogue):
     * re-scoping which agents see a document's evidence is an access-control
     * decision. DOM-7: missing and forbidden both 404.
     * @returns KnowmapDocumentOut Successful Response
     * @throws ApiError
     */
    public static setKnowmapDocumentAgentsApiKnowmapDocumentsDocumentIdAgentsPatch({
        documentId,
        requestBody,
    }: {
        documentId: string,
        requestBody: KnowmapDocumentAgentsPatchIn,
    }): CancelablePromise<KnowmapDocumentOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/knowmap-documents/{document_id}/agents',
            path: {
                'document_id': documentId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Knowmap Configs
     * @returns KnowmapConfigOut Successful Response
     * @throws ApiError
     */
    public static listKnowmapConfigsApiProjectsProjectIdKnowmapConfigsGet({
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
    }): CancelablePromise<Array<KnowmapConfigOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/projects/{project_id}/knowmap-configs',
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
     * Create Knowmap Config
     * @returns KnowmapConfigOut Successful Response
     * @throws ApiError
     */
    public static createKnowmapConfigApiProjectsProjectIdKnowmapConfigsPost({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: KnowmapConfigCreateIn,
    }): CancelablePromise<KnowmapConfigOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/projects/{project_id}/knowmap-configs',
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
