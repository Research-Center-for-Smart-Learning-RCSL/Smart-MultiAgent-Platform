/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DocumentStatus } from './DocumentStatus';
import type { ScanStatus } from './ScanStatus';
export type KnowmapDocumentOut = {
    id: string;
    knowmap_config_id: string;
    filename: string;
    mime: string;
    size_bytes: number;
    sha256: string;
    status: DocumentStatus;
    scan_status: ScanStatus;
    failure_code: (string | null);
    uploaded_at: string;
    agent_ids: Array<string>;
};

