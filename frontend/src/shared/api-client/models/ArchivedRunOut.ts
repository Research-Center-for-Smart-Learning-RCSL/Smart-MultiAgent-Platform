/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A run row from the live+archive union (`list_runs?include_archive=true`).
 *
 * Distinct from `RunOut` (API-6): the archive projection omits `variables`,
 * and the `archived` flag tells the client which table the row came from.
 * Defining it explicitly means archive rows are schema-validated instead of
 * being returned as raw service dicts.
 */
export type ArchivedRunOut = {
    archived: boolean;
    ended_at: (string | null);
    id: string;
    started_at: string;
    started_by_user_id: (string | null);
    state: string;
    trigger_type: (string | null);
    workflow_id: (string | null);
};

