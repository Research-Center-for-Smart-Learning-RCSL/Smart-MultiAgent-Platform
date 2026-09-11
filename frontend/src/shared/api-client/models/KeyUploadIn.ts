/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiKeyProvider } from './ApiKeyProvider';
export type KeyUploadIn = {
    provider: ApiKeyProvider;
    name: string;
    secret: string;
    config?: (Record<string, any> | null);
};

