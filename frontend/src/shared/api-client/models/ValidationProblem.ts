/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ValidationFieldError } from './ValidationFieldError';
/**
 * Published schema for the global request-validation Problem response.
 */
export type ValidationProblem = {
    detail: string;
    field_errors: Array<ValidationFieldError>;
    instance: string;
    status: 422;
    title: string;
    type: string;
};

