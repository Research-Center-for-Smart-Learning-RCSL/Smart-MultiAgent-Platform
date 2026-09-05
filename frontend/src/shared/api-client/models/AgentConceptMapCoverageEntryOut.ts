/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { BuildState } from './BuildState';
/**
 * One Concept Map covering an agent (Phase 4α R11.09, read-only).
 */
export type AgentConceptMapCoverageEntryOut = {
    config_id: string;
    owner_kind: 'agent_group' | 'chatroom' | 'workspace';
    owner_id: string;
    owner_name: string;
    active: boolean;
    last_build_state: BuildState;
    last_build_at: (string | null);
    last_build_error: (string | null);
};

