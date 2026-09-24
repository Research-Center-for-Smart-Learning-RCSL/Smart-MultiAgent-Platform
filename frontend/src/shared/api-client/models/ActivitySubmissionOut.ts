/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ActivitySubmissionOut = {
    id: string;
    session_id: string;
    activity_type_id: string;
    chatroom_id: string;
    attempt_no: number;
    validation_status: string;
    is_valid: (boolean | null);
    error_class: (string | null);
    sub_scores: Record<string, any>;
    latency_ms: (number | null);
    created_at: (string | null);
};

