/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { BuildState } from './BuildState';
import type { ChunkStrategy } from './ChunkStrategy';
/**
 * PATCH response — the config plus any agents auto-detached by a builder
 * key-group change that collided with their consumer group (R11.25 / F-14).
 *
 * Dedicated subclass so the shared GET/create ``KnowmapConfigOut`` shape stays
 * unbroadened. ``detached_agent_ids`` is empty on any non-colliding change.
 */
export type KnowmapConfigPatchOut = {
    builder_key_group_id: string;
    chunk_params: Record<string, any>;
    chunk_strategy: ChunkStrategy;
    created_at: string;
    deleted_at: (string | null);
    detached_agent_ids?: Array<string>;
    embed_dim: (number | null);
    embed_model: (string | null);
    embed_provider: (string | null);
    id: string;
    last_build_at: (string | null);
    last_build_error: (string | null);
    last_build_state: BuildState;
    name: string;
    project_id: string;
};

