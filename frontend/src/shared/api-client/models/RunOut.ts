/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { RunState } from './RunState';
export type RunOut = {
    id: string;
    workflow_id: string;
    trigger_type: string;
    started_by_user_id: (string | null);
    state: RunState;
    variables: Record<string, any>;
    started_at: string;
    ended_at: (string | null);
};

