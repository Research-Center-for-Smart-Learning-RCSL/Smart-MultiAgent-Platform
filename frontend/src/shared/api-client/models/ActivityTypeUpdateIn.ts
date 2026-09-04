/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ValidatorKind } from './ValidatorKind';
/**
 * Full editable representation for an edit; ``key`` is intentionally absent
 * (never editable, R30.23). The edit form pre-fills and resubmits every field,
 * so the service diffs this against the stored row to decide the version bump.
 */
export type ActivityTypeUpdateIn = {
    name: string;
    payload_schema: Record<string, any>;
    validator_kind: ValidatorKind;
    validator_config?: Record<string, any>;
    retention_days?: (number | null);
    expose_payload_to_agent?: boolean;
    echo_includes_content?: boolean;
    group_config?: (Record<string, any> | null);
};

