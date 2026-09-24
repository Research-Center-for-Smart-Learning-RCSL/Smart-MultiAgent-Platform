/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * The four fields a platform admin may edit ([R30.23], Q-4).
 *
 * `key`, `payload_schema` and `validator_config` are absent by design, not
 * validated away: the moment a schema is editable from here this stops being an
 * install surface and becomes a course-authoring CMS.
 */
export type AdminPlatformActivityTypeIn = {
    echo_includes_content: boolean;
    expose_payload_to_agent: boolean;
    name: string;
    retention_days?: (number | null);
};

