/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ActivityGroupVoteOut } from './ActivityGroupVoteOut';
/**
 * A group's proposal and where its vote stands.
 *
 * ``payload`` is here because the people reading this are the ones being asked
 * to approve it; it is NOT on the room broadcast, which carries counts only
 * (AC-11). The two surfaces have different audiences and deliberately different
 * contents.
 */
export type ActivityGroupProposalOut = {
    id: string;
    chatroom_id: string;
    activation_id: string;
    activity_type_id: string;
    member_group_id: string;
    proposer_user_id: string;
    payload: Record<string, any>;
    status: string;
    required_approvals: number;
    approvals: number;
    rejections: number;
    undecided: number;
    voter_count: number;
    votes: Array<ActivityGroupVoteOut>;
    created_at: (string | null);
    expires_at: (string | null);
    resolved_at: (string | null);
    submission_id: (string | null);
};

