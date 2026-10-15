/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ProjectOwnerType } from './ProjectOwnerType';
export type ProjectOut = {
    created_at: string;
    created_by_user_id: string;
    deleted_at: (string | null);
    has_project_role?: boolean;
    id: string;
    is_moderator?: boolean;
    name: string;
    owner_id: string;
    owner_type: ProjectOwnerType;
    version: number;
};

