/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { BuildState } from './BuildState';
export type GraphRagConfigOut = {
    agent_id: (string | null);
    builder_key_group_id: string;
    created_at: string;
    deleted_at: (string | null);
    id: string;
    last_build_at: (string | null);
    last_build_error: (string | null);
    last_build_state: BuildState;
    owner_id: string;
    owner_kind: 'agent_group' | 'chatroom' | 'workspace';
    owner_name: (string | null);
    project_id: string;
    recency_half_life_days: (number | null);
    trigger_config: Record<string, any>;
};

