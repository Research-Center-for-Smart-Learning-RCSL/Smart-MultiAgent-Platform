/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentToolType } from './AgentToolType';
export type AgentToolOut = {
    id: string;
    agent_id: string;
    tool_type: AgentToolType;
    enabled: boolean;
    display_name: (string | null);
    config: Record<string, any>;
    config_warnings?: Array<string>;
    created_at: string;
};

