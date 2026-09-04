/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { BundleJobStatus } from './BundleJobStatus';
export type BundleImportStatusOut = {
    job_id: string;
    status: BundleJobStatus;
    skill_id: (string | null);
    warnings: Array<string>;
    error: (string | null);
};

