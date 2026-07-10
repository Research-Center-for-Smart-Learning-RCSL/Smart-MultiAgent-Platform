/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { RunState } from './RunState';
export type RunOut = {
    ended_at: (string | null);
    id: string;
    started_at: string;
    started_by_user_id: (string | null);
    state: RunState;
    trigger_type: string;
    variables: Record<string, any>;
    workflow_id: string;
};

