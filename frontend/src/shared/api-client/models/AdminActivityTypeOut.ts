/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ActivityTypeScope } from './ActivityTypeScope';
/**
 * One activity type, platform-wide.
 *
 * Carries `validator_config` deliberately (Q-3 of the dossier): an admin already
 * reads it through the project API by bypass, so withholding it here buys no
 * confidentiality and costs a screen switch during triage. It may hold answer
 * keys, so this model stays admin-only — it must not be reused by a
 * non-admin surface ([R30.25]).
 */
export type AdminActivityTypeOut = {
    id: string;
    project_id: (string | null);
    project_name: (string | null);
    scope: ActivityTypeScope;
    key: string;
    name: string;
    validator_kind: string;
    validator_config: Record<string, any>;
    expose_payload_to_agent: boolean;
    echo_includes_content: boolean;
    retention_days: (number | null);
    version: number;
    created_at: string;
};

