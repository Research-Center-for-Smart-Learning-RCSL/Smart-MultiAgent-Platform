/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * How one round is going, as its facilitator sees it ([R30.22]).
 *
 * Counts only. Naming who has finished is a separate privacy decision that the
 * room-creator gate does not by itself authorize, so it is not in this model
 * and must not be added to it without one.
 */
export type ActivityActivationProgressOut = {
    completed: number;
    in_progress: number;
};

