/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type RagConfigCreateIn = {
    name: string;
    chunk_strategy: 'fixed' | 'semantic';
    chunk_params?: Record<string, any>;
    embed_key_id: string;
    embed_provider: 'openai' | 'gemini' | 'voyage';
    embed_model: string;
    rerank_enabled?: boolean;
    rerank_key_id?: (string | null);
    rerank_provider?: ('cohere' | 'bge' | null);
    rerank_model?: (string | null);
    top_k?: number;
};

