/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentConceptMapCoverageOut } from '../models/AgentConceptMapCoverageOut';
import type { ConceptMapOwnerOptionOut } from '../models/ConceptMapOwnerOptionOut';
import type { GraphOut } from '../models/GraphOut';
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
     * List Configs
     * @returns GraphRagConfigOut Successful Response
     * @throws ApiError
     */
    public static listConfigsApiProjectsProjectIdGraphragConfigsGet({
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
    }): CancelablePromise<Array<GraphRagConfigOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/projects/{project_id}/graphrag-configs',
            path: {
                'project_id': projectId,
            },
            query: {
                'limit': limit,
                'offset': offset,
            },
            errors: {
                422: `Request Validation Problem`,
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
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * List Owner Options
     * Owners in the project without a Concept Map, for the overview create picker.
     * @returns ConceptMapOwnerOptionOut Successful Response
     * @throws ApiError
     */
    public static listOwnerOptionsApiProjectsProjectIdGraphragConfigsOwnerOptionsGet({
        projectId,
    }: {
        projectId: string,
    }): CancelablePromise<Array<ConceptMapOwnerOptionOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/projects/{project_id}/graphrag-configs/owner-options',
            path: {
                'project_id': projectId,
            },
            errors: {
                422: `Request Validation Problem`,
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
                422: `Request Validation Problem`,
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
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Delete Config
     * §22.8 — soft-delete a GraphRAG config and cascade its external stores.
     *
     * DOM-2: the config's entity vectors live in the shared
     * ``graphrag_{project_id}`` Qdrant collection tagged with ``config_id``.
     * The old code cascaded only the Neo4j subgraph, so those vectors leaked
     * forever — and, being in a collection shared with sibling configs, kept
     * surfacing in their retrieval. We now delete them via ``delete_by_config``.
     *
     * DOM-4: the soft delete + audit row are committed *first* — that commit is
     * the point of no return — and only then are the irreversible Neo4j +
     * Qdrant deletes attempted, best-effort, recorded in a follow-up audit row.
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
                422: `Request Validation Problem`,
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
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Read Graph
     * Read a bounded view of the config's knowledge graph (viz P0).
     *
     * AuthZ matches ``read_config``: membership at the config's project. The
     * facade assembles the read model; the limit is clamped so a large graph can
     * never stream an unbounded payload to the browser.
     * @returns GraphOut Successful Response
     * @throws ApiError
     */
    public static readGraphApiGraphragConfigIdGraphGet({
        configId,
        limit = 500,
    }: {
        configId: string,
        limit?: number,
    }): CancelablePromise<GraphOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/graphrag/{config_id}/graph',
            path: {
                'config_id': configId,
            },
            query: {
                'limit': limit,
            },
            errors: {
                422: `Request Validation Problem`,
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
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Read Agent Concept Map Coverage
     * Read-only coverage: every Concept Map that could feed an agent's retrieval.
     *
     * Transparency, not an attach (R11.09) — the maps of the agent's chatrooms, the
     * agent_groups it belongs to, and those chatrooms' workspaces, each flagged with
     * whether it is currently active. AuthZ matches ``read_config``: membership at
     * the agent's project.
     * @returns AgentConceptMapCoverageOut Successful Response
     * @throws ApiError
     */
    public static readAgentConceptMapCoverageApiAgentsAgentIdConceptMapCoverageGet({
        agentId,
    }: {
        agentId: string,
    }): CancelablePromise<AgentConceptMapCoverageOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/agents/{agent_id}/concept-map-coverage',
            path: {
                'agent_id': agentId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
}
