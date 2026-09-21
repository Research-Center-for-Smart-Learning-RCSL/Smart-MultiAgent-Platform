/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiKeyProvider } from './ApiKeyProvider';
export type GroupOut = {
    id: string;
    project_id: string;
    name: string;
    created_at: string;
    member_count?: number;
    providers?: Array<ApiKeyProvider>;
};

