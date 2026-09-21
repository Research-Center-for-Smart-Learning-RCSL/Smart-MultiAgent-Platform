/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type RagConfigOut = {
    id: string;
    project_id: string;
    name: string;
    chunk_strategy: string;
    chunk_params: Record<string, any>;
    embed_key_id: (string | null);
    embed_provider: string;
    embed_model: string;
    rerank_enabled: boolean;
    rerank_key_id: (string | null);
    rerank_provider: (string | null);
    rerank_model: (string | null);
    top_k: number;
    created_at: string;
    deleted_at: (string | null);
};

