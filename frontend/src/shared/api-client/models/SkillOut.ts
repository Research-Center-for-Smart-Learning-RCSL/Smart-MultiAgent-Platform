/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SkillScope } from './SkillScope';
export type SkillOut = {
    allowed_tools: Array<string>;
    body: string;
    body_sha256: string;
    bundle_sha256: (string | null);
    created_at: string;
    created_by: (string | null);
    deleted_at: (string | null);
    description: string;
    diverged: boolean;
    id: string;
    name: string;
    owner_id: (string | null);
    requires: Array<string>;
    scope: SkillScope;
    source: string;
    version: number;
};

