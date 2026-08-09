/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * What a candidate policy would block.
 *
 * Lets the admin form warn before a tightening strands a class ([R30.30]).
 * ``violating_activations`` counts activities running at this moment whose type
 * the candidate would refuse — they keep running, because enforcement is at
 * authoring and activation start, so this is the number an admin tightening for
 * a consent reason has to see before saving.
 *
 * ``approximate`` is true when either scan hit its bound, so the counts are
 * floors rather than a silent truncation.
 */
export type AdminPolicyImpactOut = {
    approximate: boolean;
    violating_activations: number;
    violating_types: number;
};

