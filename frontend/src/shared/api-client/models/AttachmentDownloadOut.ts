/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AttachmentStatus } from './AttachmentStatus';
import type { ScanStatus } from './ScanStatus';
export type AttachmentDownloadOut = {
    id: string;
    chatroom_id: (string | null);
    message_id: (string | null);
    filename: string;
    mime: string;
    size_bytes: number;
    status: AttachmentStatus;
    scan_status: ScanStatus;
    url: string;
};

