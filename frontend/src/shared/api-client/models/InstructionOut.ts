/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { InstructionState } from './InstructionState';
export type InstructionOut = {
    chain_id: string;
    depth: number;
    id: string;
    issued_at: string;
    issuer_agent_id: string;
    path: Array<string>;
    payload: Record<string, any>;
    resolved_at: (string | null);
    state: InstructionState;
    target_agent_id: string;
};

