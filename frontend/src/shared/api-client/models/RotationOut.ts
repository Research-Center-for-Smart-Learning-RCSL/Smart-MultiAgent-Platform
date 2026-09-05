/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type RotationOut = {
    retry_initial_delay_ms: number;
    retry_jitter_pct: number;
    retry_max: number;
    retry_max_delay_ms: number;
    retry_multiplier: number;
    retry_on_error: boolean;
    rotate_on_error_codes: Array<number>;
    rotate_on_token_quota: boolean;
};

