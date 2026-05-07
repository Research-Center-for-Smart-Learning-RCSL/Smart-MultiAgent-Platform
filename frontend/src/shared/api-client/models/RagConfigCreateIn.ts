/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type RagConfigCreateIn = {
    chunk_params?: Record<string, any>;
    chunk_strategy: 'fixed' | 'semantic';
    embed_key_id: string;
    embed_model: string;
    embed_provider: 'openai' | 'gemini' | 'voyage';
    name: string;
    rerank_enabled?: boolean;
    rerank_key_id?: (string | null);
    rerank_model?: (string | null);
    rerank_provider?: ('cohere' | null);
    top_k?: number;
};

