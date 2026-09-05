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
    id: string;
    project_id: string;
    name: string;
    builder_key_group_id: string;
    chunk_strategy: ChunkStrategy;
    chunk_params: Record<string, any>;
    embed_provider: (string | null);
    embed_model: (string | null);
    embed_dim: (number | null);
    last_build_state: BuildState;
    last_build_at: (string | null);
    last_build_error: (string | null);
    created_at: string;
    deleted_at: (string | null);
    detached_agent_ids?: Array<string>;
};

