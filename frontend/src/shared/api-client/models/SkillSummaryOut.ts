/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SkillScope } from './SkillScope';
/**
 * A skill without its body — what a listing needs.
 *
 * The body is bounded only by `_MAX_BODY` (256 KiB) and `limit` reaches 500, so
 * including it here would let one request return ~128 MB to render a menu of names. It
 * is served by the detail endpoint, one skill at a time, to the caller who asked for it.
 */
export type SkillSummaryOut = {
    allowed_tools: Array<string>;
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

