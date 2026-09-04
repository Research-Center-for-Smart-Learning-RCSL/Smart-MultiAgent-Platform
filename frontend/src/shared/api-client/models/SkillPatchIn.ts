/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Every field optional; missing means unchanged.
 *
 * `name` is **absent by construction, not optional**: it is the key the model invokes,
 * the key bindings and bundle exports are addressed by, and the directory name under
 * /workspace/skills/. Renaming is a copy (Q-11/AC-39). With `extra="forbid"` a client
 * that sends `name` gets a 422 rather than a silent no-op — which is the point: a
 * rename that appears to work but does not is worse than a rejection.
 */
export type SkillPatchIn = {
    description?: (string | null);
    body?: (string | null);
    requires?: (Array<string> | null);
    allowed_tools?: (Array<string> | null);
};

