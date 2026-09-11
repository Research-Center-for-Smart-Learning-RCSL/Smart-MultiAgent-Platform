/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ValidationFieldError } from './ValidationFieldError';
/**
 * Published schema for a 422 Problem response.
 *
 * Two producers share this status on the same operation: the global
 * request-validation handler, which always carries `field_errors`, and a
 * bounded context's domain error map (e.g. `auth/password-weak`), which never
 * does. `field_errors` is therefore optional here. Declaring it required
 * would publish a guarantee the domain path does not keep, and a client
 * trusting it would dereference a missing member. Distinguish the two by
 * `type`: only the request-validation problem uses `problems/validation`.
 */
export type ValidationProblem = {
    detail: string;
    field_errors?: (Array<ValidationFieldError> | null);
    instance: string;
    status: 422;
    title: string;
    type: string;
};

