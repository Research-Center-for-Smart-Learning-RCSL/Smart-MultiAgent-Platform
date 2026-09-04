/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { GraphRagTriggerConfig } from './GraphRagTriggerConfig';
/**
 * Partial update — all fields optional. Omitted = unchanged.
 */
export type GraphRagConfigPatchIn = {
    builder_key_group_id?: (string | null);
    trigger_config?: (GraphRagTriggerConfig | null);
    recency_half_life_days?: (number | null);
};

