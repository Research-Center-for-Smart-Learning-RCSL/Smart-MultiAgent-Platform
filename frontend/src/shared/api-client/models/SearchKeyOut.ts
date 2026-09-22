/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ProbeStatus } from './ProbeStatus';
import type { SearchProvider } from './SearchProvider';
export type SearchKeyOut = {
    config: Record<string, any>;
    created_at: string;
    id: string;
    is_active: boolean;
    last_test_at: (string | null);
    masked_preview: string;
    project_id: string;
    provider: SearchProvider;
    test_error: (string | null);
    test_status: ProbeStatus;
};

