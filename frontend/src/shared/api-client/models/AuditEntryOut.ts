/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type AuditEntryOut = {
    id: number;
    actor_user_id: (string | null);
    actor_ip: (string | null);
    action: string;
    resource_type: (string | null);
    resource_id: (string | null);
    metadata: Record<string, any>;
    session_id: (string | null);
    request_id: (string | null);
    created_at: string;
};

