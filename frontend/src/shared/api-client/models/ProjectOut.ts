/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ProjectOwnerType } from './ProjectOwnerType';
export type ProjectOut = {
    id: string;
    name: string;
    owner_type: ProjectOwnerType;
    owner_id: string;
    created_by_user_id: string;
    version: number;
    created_at: string;
    deleted_at: (string | null);
    is_moderator?: boolean;
};

