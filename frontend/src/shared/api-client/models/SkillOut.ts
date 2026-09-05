/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SkillScope } from './SkillScope';
export type SkillOut = {
    id: string;
    scope: SkillScope;
    owner_id: (string | null);
    name: string;
    description: string;
    body_sha256: string;
    source: string;
    bundle_sha256: (string | null);
    diverged: boolean;
    requires: Array<string>;
    allowed_tools: Array<string>;
    created_by: (string | null);
    version: number;
    created_at: string;
    deleted_at: (string | null);
    body: string;
};

