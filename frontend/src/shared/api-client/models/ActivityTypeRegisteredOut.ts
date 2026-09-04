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
    id: string;
    project_id: (string | null);
    scope: ActivityTypeScope;
    key: string;
    name: string;
    payload_schema: Record<string, any>;
    validator_kind: ValidatorKind;
    validator_config: Record<string, any>;
    retention_days: (number | null);
    expose_payload_to_agent: boolean;
    echo_includes_content: boolean;
    created_at: (string | null);
    group_config?: (Record<string, any> | null);
    shadowed_by_platform?: boolean;
};

