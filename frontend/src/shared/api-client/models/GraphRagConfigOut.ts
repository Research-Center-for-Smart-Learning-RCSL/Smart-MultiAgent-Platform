/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { BuildState } from './BuildState';
export type GraphRagConfigOut = {
    id: string;
    project_id: string;
    owner_kind: 'agent_group' | 'chatroom' | 'workspace';
    owner_id: string;
    owner_name: (string | null);
    agent_id: (string | null);
    builder_key_group_id: string;
    trigger_config: Record<string, any>;
    recency_half_life_days: (number | null);
    last_build_state: BuildState;
    last_build_at: (string | null);
    last_build_error: (string | null);
    created_at: string;
    deleted_at: (string | null);
};

