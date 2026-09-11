/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type app__api__v1__key_groups__MemberPatchIn = {
    max_input_tokens_per_hour?: (number | null);
    max_output_tokens_per_hour?: (number | null);
    max_requests_per_hour?: (number | null);
    priority?: (number | null);
    retry_initial_delay_ms?: (number | null);
    retry_jitter_pct?: (number | null);
    retry_max?: (number | null);
    retry_max_delay_ms?: (number | null);
    retry_multiplier?: (number | null);
    retry_on_error?: (boolean | null);
    rotate_on_error_codes?: (Array<number> | null);
    rotate_on_token_quota?: (boolean | null);
};

