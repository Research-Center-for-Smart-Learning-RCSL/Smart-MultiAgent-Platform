/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type AgentRef = {
    agent_id: string;
    role?: ('normal' | 'observer' | null);
    may_control_activities?: (boolean | null);
    activity_type_allowlist?: (Array<string> | null);
    may_read_drafts?: (boolean | null);
};

