/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * New text for an existing file.
 *
 * `path` is absent for the same reason `SkillPatchIn` omits `name`: it is the key
 * `SKILL.md` references the file by, so a rename is a delete plus an add, not an edit.
 */
export type SkillFilePatchIn = {
    content: string;
};

