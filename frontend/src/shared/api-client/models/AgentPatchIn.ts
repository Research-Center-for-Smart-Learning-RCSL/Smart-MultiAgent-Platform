/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type AgentPatchIn = {
    a2a_enabled?: (boolean | null);
    context_mode?: ('general' | 'compact' | null);
    context_token_cap?: (number | null);
    effort?: ('low' | 'medium' | 'high' | null);
    key_group_id?: (string | null);
    knowmap_config_id?: (string | null);
    model_hint?: ('claude' | 'openai' | 'gemini' | null);
    model_id?: (string | null);
    name?: (string | null);
    rag_config_id?: (string | null);
    seed?: (number | null);
    system_prompt?: (string | null);
    temperature?: (number | null);
    top_p?: (number | null);
    wakeup_config?: (Record<string, any> | null);
    workflow_capabilities?: (Record<string, any> | null);
};

