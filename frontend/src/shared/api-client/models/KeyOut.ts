/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiKeyProvider } from './ApiKeyProvider';
import type { ProbeStatus } from './ProbeStatus';
export type KeyOut = {
    config?: Record<string, any>;
    created_at: string;
    id: string;
    last_test_at: (string | null);
    masked_preview: string;
    name: string;
    provider: ApiKeyProvider;
    test_error: (string | null);
    test_status: ProbeStatus;
};

