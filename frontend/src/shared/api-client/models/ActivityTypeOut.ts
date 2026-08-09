/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ActivityTypeScope } from './ActivityTypeScope';
import type { ValidatorKind } from './ValidatorKind';
export type ActivityTypeOut = {
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
    validator_config: Record<string, any>;
    validator_kind: ValidatorKind;
};

