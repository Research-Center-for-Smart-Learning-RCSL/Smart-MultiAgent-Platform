/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { StepState } from './StepState';
export type StepOut = {
    id: string;
    run_id: string;
    node_id: string;
    state: StepState;
    started_at: string;
    ended_at: (string | null);
    input: Record<string, any>;
    output: Record<string, any>;
    error: (string | null);
};

