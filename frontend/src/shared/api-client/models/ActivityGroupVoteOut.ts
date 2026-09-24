/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One pinned voter's decision ([R30.42]).
 *
 * Only ever populated for a caller entitled to the per-person record: the
 * proposal's pinned voters and the room creator. Every other reader gets the
 * counts and an empty list — and no agent reaches this surface at all.
 */
export type ActivityGroupVoteOut = {
    approve: boolean;
    created_at: (string | null);
    user_id: string;
};

