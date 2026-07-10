/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { BuildState } from './BuildState';
import type { ChunkStrategy } from './ChunkStrategy';
export type KnowmapConfigOut = {
    builder_key_group_id: string;
    chunk_params: Record<string, any>;
    chunk_strategy: ChunkStrategy;
    created_at: string;
    deleted_at: (string | null);
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

