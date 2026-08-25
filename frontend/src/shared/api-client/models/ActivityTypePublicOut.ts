/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * The participant rendering contract (R30.26): identity, key, display
 * name, payload schema, and the consent fraction — and nothing else. No
 * `validator_config`, which is confidential to Project Owners (R30.25).
 * Reachable through the room-access chain, never through project membership.
 */
export type ActivityTypePublicOut = {
    group_config?: (Record<string, any> | null);
    id: string;
    key: string;
    name: string;
    payload_schema: Record<string, any>;
};

