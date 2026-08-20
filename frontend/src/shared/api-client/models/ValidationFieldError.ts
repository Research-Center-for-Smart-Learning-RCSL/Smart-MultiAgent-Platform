/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One request-validation failure safe to expose on the public wire.
 *
 * `path` is relative to `location`, not absolute: the request-part prefix that
 * FastAPI puts at the head of `loc` is carried in `location` instead. Without
 * the split, a path parameter and a body field of the same name are
 * indistinguishable, and a client mapping errors onto form fields attaches the
 * wrong one.
 */
export type ValidationFieldError = {
    location: 'body' | 'query' | 'path' | 'header' | 'cookie';
    message: string;
    path: string;
};

