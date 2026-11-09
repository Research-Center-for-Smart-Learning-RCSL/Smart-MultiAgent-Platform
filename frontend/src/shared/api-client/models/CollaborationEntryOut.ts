/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type CollaborationEntryOut = {
    about_message_id: (string | null);
    created_at: string;
    decision: ('accepted' | 'partly_accepted' | 'declined' | null);
    error: string;
    error_kind: 'content' | 'strategy';
    from_agent_name: (string | null);
    id: string;
    kind: 'analyst_notice';
    reason: (string | null);
    responded_at: (string | null);
    room_id: string;
    room_name: string;
    status: 'pending' | 'answered' | 'unanswered';
    suggestion: string;
    to_agent_name: (string | null);
};

