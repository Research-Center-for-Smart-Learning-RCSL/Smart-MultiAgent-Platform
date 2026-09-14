/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApprovalMode } from './ApprovalMode';
import type { ApprovalState } from './ApprovalState';
export type ApprovalOut = {
    id: string;
    workflow_run_id: string;
    mode: ApprovalMode;
    leader_agent_id: string;
    approver_agent_ids: Array<string>;
    timeout_seconds: number;
    state: ApprovalState;
    started_at: string;
    ended_at: (string | null);
};

