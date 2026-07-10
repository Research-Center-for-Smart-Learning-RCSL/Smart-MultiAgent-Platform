/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FileOut } from './FileOut';
import type { KeyMetaOut } from './KeyMetaOut';
export type AssistantConfigOut = {
    daily_request_limit_per_user: number;
    enabled: boolean;
    files: Array<FileOut>;
    hide_platform_templates: boolean;
    key: (KeyMetaOut | null);
    key_id: (string | null);
    key_revoked: boolean;
    model_id: (string | null);
    scope: string;
    system_prompt: string;
    version: number;
};

