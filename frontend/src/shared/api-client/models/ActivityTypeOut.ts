/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ActivityTypeScope } from './ActivityTypeScope';
import type { ValidatorKind } from './ValidatorKind';
export type ActivityTypeOut = {
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
};

