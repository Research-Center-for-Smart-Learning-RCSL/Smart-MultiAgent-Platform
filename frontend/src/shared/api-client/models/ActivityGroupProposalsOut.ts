/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ActivityGroupProposalOut } from './ActivityGroupProposalOut';
import type { ActivityMemberGroupRefOut } from './ActivityMemberGroupRefOut';
/**
 * One round's group state for one caller.
 *
 * ``eligible_groups`` is the caller's own bound-group membership, which is
 * also the participant surface's only signal that group mode applies at all —
 * an empty list means this caller submits individually, whatever the type
 * declares.
 */
export type ActivityGroupProposalsOut = {
    items: Array<ActivityGroupProposalOut>;
    eligible_groups?: Array<ActivityMemberGroupRefOut>;
};

