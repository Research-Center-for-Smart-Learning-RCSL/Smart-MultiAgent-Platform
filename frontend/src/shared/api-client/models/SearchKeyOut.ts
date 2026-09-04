/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ProbeStatus } from './ProbeStatus';
import type { SearchProvider } from './SearchProvider';
export type SearchKeyOut = {
    id: string;
    project_id: string;
    provider: SearchProvider;
    masked_preview: string;
    test_status: ProbeStatus;
    test_error: (string | null);
    last_test_at: (string | null);
    is_active: boolean;
    config: Record<string, any>;
    created_at: string;
};

