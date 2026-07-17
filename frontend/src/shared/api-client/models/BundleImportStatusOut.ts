/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { BundleJobStatus } from './BundleJobStatus';
export type BundleImportStatusOut = {
    error: (string | null);
    job_id: string;
    skill_id: (string | null);
    status: BundleJobStatus;
    warnings: Array<string>;
};

