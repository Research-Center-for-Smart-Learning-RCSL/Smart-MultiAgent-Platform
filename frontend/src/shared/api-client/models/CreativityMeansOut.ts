/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Means over the participant's latest scored attempt per round, in this room.
 */
export type CreativityMeansOut = {
    convergence: (number | null);
    flexibility: (number | null);
    fluency: (number | null);
    last_reason: (string | null);
    last_status: ('pending' | 'scored' | 'skipped' | 'error' | null);
    originality: (number | null);
    scored_rounds: number;
};

