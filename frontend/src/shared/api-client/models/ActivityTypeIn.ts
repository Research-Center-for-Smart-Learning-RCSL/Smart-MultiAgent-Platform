/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ValidatorKind } from './ValidatorKind';
export type ActivityTypeIn = {
    echo_includes_content?: boolean;
    expose_payload_to_agent?: boolean;
    key: string;
    name: string;
    payload_schema: Record<string, any>;
    retention_days?: (number | null);
    validator_config?: Record<string, any>;
    validator_kind: ValidatorKind;
};

