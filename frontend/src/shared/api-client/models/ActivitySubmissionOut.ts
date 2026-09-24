/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ActivitySubmissionOut = {
    activity_type_id: string;
    attempt_no: number;
    chatroom_id: string;
    created_at: (string | null);
    error_class: (string | null);
    id: string;
    is_valid: (boolean | null);
    latency_ms: (number | null);
    session_id: string;
    sub_scores: Record<string, any>;
    validation_status: string;
};

