/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { BundleJobStatus } from './BundleJobStatus';
/**
 * The 202 body for import/export — a task id to poll.
 */
export type BundleJobOut = {
    job_id: string;
    status: BundleJobStatus;
};

