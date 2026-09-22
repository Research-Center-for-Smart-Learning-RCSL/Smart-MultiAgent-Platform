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
    activation_id: string;
    activity_type_id: string;
    approvals: number;
    chatroom_id: string;
    created_at: (string | null);
    expires_at: (string | null);
    id: string;
    member_group_id: string;
    payload: Record<string, any>;
    proposer_user_id: string;
    rejections: number;
    required_approvals: number;
    resolved_at: (string | null);
    status: string;
    submission_id: (string | null);
    undecided: number;
    voter_count: number;
    votes: Array<ActivityGroupVoteOut>;
};

