/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiKeyProvider } from './ApiKeyProvider';
import type { ProbeStatus } from './ProbeStatus';
/**
 * `KeyOut` plus the active-carry count. Exposed only on the my-keys list so
 * the shared `KeyOut` (also returned by the project-carried surface) stays free
 * of a field that has no meaning there.
 */
export type KeyListOut = {
    id: string;
    provider: ApiKeyProvider;
    name: string;
    masked_preview: string;
    test_status: ProbeStatus;
    test_error: (string | null);
    last_test_at: (string | null);
    created_at: string;
    config?: Record<string, any>;
    project_count: number;
};

