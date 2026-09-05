/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiKeyProvider } from './ApiKeyProvider';
import type { ProbeStatus } from './ProbeStatus';
export type KeyOut = {
    id: string;
    provider: ApiKeyProvider;
    name: string;
    masked_preview: string;
    test_status: ProbeStatus;
    test_error: (string | null);
    last_test_at: (string | null);
    created_at: string;
    config?: Record<string, any>;
};

