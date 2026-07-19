/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { KnowmapConfigOut } from '../models/KnowmapConfigOut';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class KnowmapAdminService {
    /**
     * Admin Reset
     * Mirrors graphrag.py's admin_reset, down to the platform-admin check.
     *
     * Knowledge Maps had no reset until
     * docs/tasks/2026-07-17-graphrag-reset-expired-recovery/, which made
     * ``recovery_unavailable`` reachable here — a state outside the reconciler's sweep
     * set, so without this route an operator's only option would have been a rebuild.
     *
     * AuthZ deliberately matches the Concept Map's: platform admin, no project scoping.
     * That is weaker than every other route in this module (FU-2 in the dossier), and
     * diverging here would have made the two resets inconsistent while fixing neither.
     * @returns KnowmapConfigOut Successful Response
     * @throws ApiError
     */
    public static adminResetApiAdminKnowmapConfigIdResetPost({
        configId,
        force = false,
    }: {
        configId: string,
        /**
         * Override lock contention and, when 2PC compensation cannot complete, still force idle while recording the incomplete outcome (R11a.02). Accepts data loss: forcing idle also makes the graph readable again, including a partially applied build that can never be rolled back, so those facts re-enter agent context and the graph view. Rebuilding from recovery_unavailable does not require this flag.
         */
        force?: boolean,
    }): CancelablePromise<KnowmapConfigOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/admin/knowmap/{config_id}/reset',
            path: {
                'config_id': configId,
            },
            query: {
                'force': force,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
