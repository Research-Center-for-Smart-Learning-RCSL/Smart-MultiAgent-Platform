/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FileOut } from './FileOut';
import type { KeyMetaOut } from './KeyMetaOut';
export type AssistantConfigOut = {
    id: string;
    scope: string;
    name: string;
    description: string;
    enabled: boolean;
    persona_prompt: string;
    system_prompt: string;
    key_id: (string | null);
    key: (KeyMetaOut | null);
    key_revoked: boolean;
    model_id: (string | null);
    daily_request_limit_per_user: number;
    hide_platform_templates: boolean;
    version: number;
    files: Array<FileOut>;
};

