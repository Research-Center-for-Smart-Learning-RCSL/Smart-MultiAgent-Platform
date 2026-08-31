/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { EmailDomainPolicyMode } from './EmailDomainPolicyMode';
import type { EmailDomainPolicyRolloutState } from './EmailDomainPolicyRolloutState';
/**
 * The stored policy plus the rollout facts the Admin UI needs.
 *
 * ``rollout_state`` is not decoration: the form is read-only outside `active`,
 * and without it the UI could only discover that by attempting a write and
 * reading a 409. ``legacy_mirrored_version`` is the rollback marker — equal to
 * ``version`` means the legacy triple has been written and read back, which is
 * the documented precondition for starting an old image.
 */
export type EmailDomainPolicyOut = {
    allow: Array<string>;
    deny: Array<string>;
    editable: boolean;
    legacy_mirrored_version: (number | null);
    mode: EmailDomainPolicyMode;
    rollout_state: EmailDomainPolicyRolloutState;
    updated_at: (string | null);
    version: number;
};

