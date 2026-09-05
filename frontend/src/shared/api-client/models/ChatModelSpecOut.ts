/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Per-model request-shaping capabilities (R9.03a) — what the agent-config
 * form needs to disable a control the selected model refuses, and to bound
 * the context-token-cap input by the model's own window rather than the
 * provider's.
 */
export type ChatModelSpecOut = {
    model_id: string;
    context_limit: number;
    accepts_effort: boolean;
    effort_values: Array<string>;
    accepts_sampling: boolean;
    accepts_seed: boolean;
    accepts_vision: boolean;
    uses_completion_token_field: boolean;
    effort_conflicts_with_tools: boolean;
    source_url: string;
    verified_on: string;
};

