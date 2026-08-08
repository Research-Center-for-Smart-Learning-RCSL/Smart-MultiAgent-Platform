/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
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
    created_at: string;
    echo_includes_content: boolean;
    expose_payload_to_agent: boolean;
    id: string;
    key: string;
    name: string;
    project_id: string;
    project_name: (string | null);
    retention_days: (number | null);
    validator_config: Record<string, any>;
    validator_kind: string;
    version: number;
};

