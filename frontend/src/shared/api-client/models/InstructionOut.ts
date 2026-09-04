/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { InstructionState } from './InstructionState';
export type InstructionOut = {
    id: string;
    chain_id: string;
    path: Array<string>;
    depth: number;
    issuer_agent_id: string;
    target_agent_id: string;
    payload: Record<string, any>;
    state: InstructionState;
    issued_at: string;
    resolved_at: (string | null);
};

