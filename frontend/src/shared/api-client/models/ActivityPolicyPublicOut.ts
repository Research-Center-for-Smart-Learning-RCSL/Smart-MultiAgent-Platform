/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * The platform governance policy as an author needs to see it ([R30.29]).
 *
 * Any authenticated caller, like the validator listing above: the policy is
 * platform configuration, not a secret, and an owner would learn the same facts
 * from a 409 on their first save. Reading it up front is what lets the authoring
 * form pre-fill a default and disable a locked switch instead of letting the
 * owner fill in a form that cannot be accepted.
 *
 * Deliberately omits ``updated_by_user_id`` — who set the policy is an admin
 * concern and is on the admin surface.
 */
export type ActivityPolicyPublicOut = {
    echo_includes_content_default: boolean;
    echo_includes_content_locked: boolean;
    expose_payload_to_agent_default: boolean;
    expose_payload_to_agent_locked: boolean;
    retention_days_default: (number | null);
    retention_days_max: (number | null);
};

