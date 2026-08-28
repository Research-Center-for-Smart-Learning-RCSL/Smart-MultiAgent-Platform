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
    accepts_effort: boolean;
    accepts_sampling: boolean;
    accepts_vision: boolean;
    context_limit: number;
    effort_conflicts_with_tools: boolean;
    effort_values: Array<string>;
    model_id: string;
    source_url: string;
    uses_completion_token_field: boolean;
    verified_on: string;
};

