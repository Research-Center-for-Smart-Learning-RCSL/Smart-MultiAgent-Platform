/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Grant or revoke one bound agent's activity start/end authority ([R30.37]).
 *
 * ``activity_type_ids`` is required whenever ``granted`` is true and is validated
 * against the room's project before anything is written — an unresolvable id is a
 * 422, never a silently dropped entry. On a revoke it is ignored: the stored
 * allowlist is left in place so the teacher's selection survives a re-grant.
 */
export type AgentActivityControlIn = {
    granted: boolean;
    activity_type_ids?: Array<string>;
};

