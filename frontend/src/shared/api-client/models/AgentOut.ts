/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentEffort } from './AgentEffort';
import type { AgentModelHint } from './AgentModelHint';
import type { ContextMode } from './ContextMode';
export type AgentOut = {
    a2a_enabled: boolean;
    context_mode: ContextMode;
    context_token_cap: (number | null);
    created_at: string;
    deleted_at: (string | null);
    effort: (AgentEffort | null);
    id: string;
    key_group_id: string;
    knowmap_config_id: (string | null);
    model_hint: AgentModelHint;
    model_id: (string | null);
    name: string;
    project_id: string;
    rag_config_id: (string | null);
    seed: (number | null);
    system_prompt: string;
    temperature: (number | null);
    top_p: (number | null);
    version: number;
    wakeup_config: Record<string, any>;
    workflow_capabilities: Record<string, any>;
};

