/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * `KeyOut` plus the active-carry count. Exposed only on the my-keys list so
 * the shared `KeyOut` (also returned by the project-carried surface) stays free
 * of a field that has no meaning there.
 */
export type KeyListOut = {
    created_at: string;
    id: string;
    last_test_at: (string | null);
    masked_preview: string;
    name: string;
    project_count: number;
    provider: string;
    test_error: (string | null);
    test_status: string;
};

