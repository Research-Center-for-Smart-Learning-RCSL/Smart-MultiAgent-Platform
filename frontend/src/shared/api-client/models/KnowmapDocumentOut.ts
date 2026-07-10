/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DocumentStatus } from './DocumentStatus';
import type { ScanStatus } from './ScanStatus';
export type KnowmapDocumentOut = {
    agent_ids: Array<string>;
    filename: string;
    id: string;
    knowmap_config_id: string;
    mime: string;
    scan_status: ScanStatus;
    sha256: string;
    size_bytes: number;
    status: DocumentStatus;
    uploaded_at: string;
};

