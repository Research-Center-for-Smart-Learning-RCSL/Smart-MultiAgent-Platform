/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentEffort } from './AgentEffort';
export type AgentPatchIn = {
    name?: (string | null);
    model_hint?: ('claude' | 'openai' | 'gemini' | 'openai_compat' | null);
    model_id?: (string | null);
    effort?: (AgentEffort | null);
    key_group_id?: (string | null);
    system_prompt?: (string | null);
    rag_config_id?: (string | null);
    knowmap_config_id?: (string | null);
    context_mode?: ('general' | 'compact' | null);
    context_token_cap?: (number | null);
    skill_index_token_cap?: (number | null);
    temperature?: (number | null);
    top_p?: (number | null);
    seed?: (number | null);
    a2a_enabled?: (boolean | null);
    wakeup_config?: (Record<string, any> | null);
    workflow_capabilities?: (Record<string, any> | null);
};

