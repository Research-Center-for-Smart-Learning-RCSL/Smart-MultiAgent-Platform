/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type AgentPatchIn = {
    a2a_enabled?: (boolean | null);
    context_mode?: ('general' | 'compact' | null);
    context_token_cap?: (number | null);
    graphrag_config_id?: (string | null);
    key_group_id?: (string | null);
    model_hint?: ('claude' | 'openai' | 'gemini' | null);
    name?: (string | null);
    prompt_strategy?: ('full' | 'lazy' | null);
    rag_config_id?: (string | null);
    system_prompt?: (string | null);
    wakeup_config?: (Record<string, any> | null);
    workflow_capabilities?: (Record<string, any> | null);
};

