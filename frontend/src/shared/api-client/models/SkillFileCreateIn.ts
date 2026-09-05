/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A UI-authored file (AC-16).
 *
 * `kind` is **absent by construction**, not optional: R31.18 derives it from the path's
 * top-level directory, and a client-chosen kind would let an uploader put a script under
 * `assets/` and have it staged, or mark a binary `reference` and have `read_skill`
 * render its bytes into the prompt. With `extra="forbid"` a client that sends one gets a
 * 422 rather than a silent no-op.
 */
export type SkillFileCreateIn = {
    path: string;
    content: string;
    mime?: string;
};

