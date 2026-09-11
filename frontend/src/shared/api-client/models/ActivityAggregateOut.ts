/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ActivityAggregateOut = {
    total: number;
    valid_count: number;
    error_count: number;
    pending_count: number;
    error_class_histogram: Record<string, number>;
    latency_avg_ms: (number | null);
    latency_min_ms: (number | null);
    latency_max_ms: (number | null);
};

