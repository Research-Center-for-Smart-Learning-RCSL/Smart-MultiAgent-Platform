/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ProjectMemberRole } from './ProjectMemberRole';
export type ProjectMemberOut = {
    user_id: string;
    email: string;
    display_name?: (string | null);
    role: ProjectMemberRole;
    joined_at: string;
    group_ids?: Array<string>;
    group_names?: Array<string>;
};

