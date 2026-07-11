/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApprovalMode } from './ApprovalMode';
import type { ApprovalState } from './ApprovalState';
export type ApprovalOut = {
    approver_agent_ids: Array<string>;
    ended_at: (string | null);
    id: string;
    leader_agent_id: string;
    mode: ApprovalMode;
    started_at: string;
    state: ApprovalState;
    timeout_seconds: number;
    workflow_run_id: string;
};

