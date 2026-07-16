/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * [R31.11] — live skills per scope, keyed by the scope's wire value.
 *
 * A map rather than four named fields: the scope set is the domain enum's, and four
 * hand-written columns would drift from it silently the day a fifth scope lands
 * (FU-14 already tracks one).
 */
export type SkillScopeCountsOut = {
    counts: Record<string, number>;
    total: number;
};

