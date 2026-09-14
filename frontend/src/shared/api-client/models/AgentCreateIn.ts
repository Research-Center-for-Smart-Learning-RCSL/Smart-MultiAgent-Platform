/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentEffort } from './AgentEffort';
export type AgentCreateIn = {
    a2a_enabled?: boolean;
    context_mode?: 'general' | 'compact';
    context_token_cap?: (number | null);
    effort?: (AgentEffort | null);
    key_group_id: string;
    knowmap_config_id?: (string | null);
    model_hint: 'claude' | 'openai' | 'gemini' | 'openai_compat';
    model_id?: (string | null);
    name: string;
    rag_config_id?: (string | null);
    seed?: (number | null);
    skill_index_token_cap?: (number | null);
    system_prompt?: string;
    temperature?: (number | null);
    top_p?: (number | null);
    wakeup_config?: Record<string, any>;
    workflow_capabilities?: Record<string, any>;
};

