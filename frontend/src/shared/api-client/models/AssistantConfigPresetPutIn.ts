/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Full editor payload for a platform preset — used for both create (POST,
 * no If-Match) and update (PUT, If-Match required).
 */
export type AssistantConfigPresetPutIn = {
    name?: string;
    description?: string;
    persona_prompt?: string;
    system_prompt?: string;
    key_id?: (string | null);
    model_id?: (string | null);
    daily_request_limit_per_user?: number;
    enabled?: boolean;
};

