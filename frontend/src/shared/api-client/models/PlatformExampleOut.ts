/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * An installed platform example as a Project Owner sees it ([R30.32]).
 *
 * Carries the two governance flags because enabling one is a consent decision:
 * ``expose_payload_to_agent`` means participant text reaches the project's
 * configured LLM provider, and the owner making that choice has to be told so at
 * the moment they make it.
 *
 * No ``validator_config`` — it may hold answer keys and is owner-confidential
 * ([R30.25]). Its absence here is not a redaction to re-add later: this listing
 * exists to choose a type, not to inspect one.
 */
export type PlatformExampleOut = {
    echo_includes_content: boolean;
    enabled: boolean;
    expose_payload_to_agent: boolean;
    id: string;
    key: string;
    name: string;
    retention_days: (number | null);
};

