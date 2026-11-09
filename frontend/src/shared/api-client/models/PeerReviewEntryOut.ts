/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type PeerReviewEntryOut = {
    attempt: number;
    created_at: string;
    decided_at: (string | null);
    draft: string;
    expire_reason: ('no_teacher' | 'no_decision' | 'binding_changed' | 'defer_limit' | 'failed' | null);
    from_agent_name: (string | null);
    id: string;
    kind: 'peer_review';
    posted_message_id: (string | null);
    reason: (string | null);
    revised_text: (string | null);
    room_id: string;
    room_name: string;
    status: 'pending' | 'approved' | 'modified' | 'rejected' | 'expired';
    to_agent_name: (string | null);
};

