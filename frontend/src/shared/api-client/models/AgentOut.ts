/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentEffort } from './AgentEffort';
import type { AgentModelHint } from './AgentModelHint';
import type { ContextMode } from './ContextMode';
export type AgentOut = {
    id: string;
    project_id: string;
    name: string;
    model_hint: AgentModelHint;
    model_id: (string | null);
    effort: (AgentEffort | null);
    key_group_id: string;
    system_prompt: string;
    rag_config_id: (string | null);
    knowmap_config_id: (string | null);
    context_mode: ContextMode;
    context_token_cap: (number | null);
    skill_index_token_cap: (number | null);
    temperature: (number | null);
    top_p: (number | null);
    seed: (number | null);
    a2a_enabled: boolean;
    wakeup_config: Record<string, any>;
    workflow_capabilities: Record<string, any>;
    version: number;
    created_at: string;
    deleted_at: (string | null);
};

