/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One bundled file's metadata.
 *
 * There is deliberately no per-file `readable` flag. AC-34's gate is whole-skill
 * (Q-18), so a badge saying one file is fine would contradict the skill it belongs to
 * being unreadable; the UI derives that state from the collection's scan statuses.
 */
export type SkillFileOut = {
    created_at: string;
    extracted_chars: number;
    id: string;
    kind: string;
    mime: string;
    path: string;
    scan_status: string;
    sha256: string;
    size_bytes: number;
    skill_id: string;
};

