/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type AgentCreateIn = {
    a2a_enabled?: boolean;
    context_mode?: 'general' | 'compact';
    context_token_cap?: (number | null);
    effort?: ('low' | 'medium' | 'high' | null);
    graphrag_config_id?: (string | null);
    key_group_id: string;
    model_hint: 'claude' | 'openai' | 'gemini';
    model_id?: (string | null);
    name: string;
    prompt_strategy?: 'full' | 'lazy';
    rag_config_id?: (string | null);
    system_prompt?: string;
    wakeup_config?: Record<string, any>;
    workflow_capabilities?: Record<string, any>;
};

