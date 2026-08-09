/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * How many live types a candidate policy would refuse to activate.
 *
 * Lets the admin form warn before a tightening strands a class ([R30.30]).
 * ``approximate`` is true when the scan hit its bound, so the count is a floor
 * rather than a silent truncation.
 */
export type AdminPolicyImpactOut = {
    approximate: boolean;
    violating_types: number;
};

