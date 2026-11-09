/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PromptRevisionSource } from './PromptRevisionSource';
export type PromptRevisionOut = {
    actor_user_id: (string | null);
    agent_version: number;
    created_at: string;
    design_run_id: (string | null);
    id: string;
    restored_from_id: (string | null);
    source: PromptRevisionSource;
    system_prompt: string;
};

