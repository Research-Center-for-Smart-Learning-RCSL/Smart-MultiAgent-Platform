/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * The platform governance policy ([R30.29]).
 *
 * ``version`` is 0 when no policy has ever been saved — the client uses that to
 * know it is creating rather than replacing, and must not send ``If-Match``.
 */
export type AdminActivityPolicyOut = {
    expose_payload_to_agent_default: boolean;
    expose_payload_to_agent_locked: boolean;
    echo_includes_content_default: boolean;
    echo_includes_content_locked: boolean;
    retention_days_default: (number | null);
    retention_days_max: (number | null);
    version: number;
    updated_at: (string | null);
    updated_by_user_id: (string | null);
};

