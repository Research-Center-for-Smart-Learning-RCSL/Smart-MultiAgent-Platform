/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ActivityTypeScope } from './ActivityTypeScope';
import type { ValidatorKind } from './ValidatorKind';
/**
 * The registration response: the created type plus any advisory warning.
 *
 * A subclass rather than a wrapper so the client keeps reading the row exactly
 * where it always did. `shadowed_by_platform` is not an error: the type WAS
 * created ([R30.02] permits the collision), but the project now holds two live
 * types under one key and everything that selects by key alone selects both.
 */
export type ActivityTypeRegisteredOut = {
    created_at: (string | null);
    echo_includes_content: boolean;
    expose_payload_to_agent: boolean;
    id: string;
    key: string;
    name: string;
    payload_schema: Record<string, any>;
    project_id: (string | null);
    retention_days: (number | null);
    scope: ActivityTypeScope;
    shadowed_by_platform?: boolean;
    validator_config: Record<string, any>;
    validator_kind: ValidatorKind;
};

