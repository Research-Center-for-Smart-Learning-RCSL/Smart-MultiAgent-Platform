/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ChatroomOut = {
    id: string;
    workspace_id: string;
    name: string;
    allow_org_members: boolean;
    allow_project_members: boolean;
    allow_project_owners_only: boolean;
    allow_guest_links: boolean;
    allow_member_groups: boolean;
    version: number;
    created_at: string;
    deleted_at: (string | null);
    created_by_user_id: (string | null);
    disclose_observers: boolean;
    observers_present: boolean;
    disclose_drafts: boolean;
    drafts_readable: boolean;
    viewer_is_guest?: boolean;
    is_moderator?: boolean;
};

