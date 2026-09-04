/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type RagConfigPatchIn = {
    name?: (string | null);
    top_k?: (number | null);
    chunk_params?: (Record<string, any> | null);
    rerank_enabled?: (boolean | null);
    rerank_key_id?: (string | null);
    rerank_provider?: ('cohere' | 'bge' | null);
    rerank_model?: (string | null);
};

