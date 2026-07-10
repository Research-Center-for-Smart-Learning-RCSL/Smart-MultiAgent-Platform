/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AttachmentStatus } from './AttachmentStatus';
import type { ScanStatus } from './ScanStatus';
export type AttachmentDownloadOut = {
    chatroom_id: (string | null);
    filename: string;
    id: string;
    message_id: (string | null);
    mime: string;
    scan_status: ScanStatus;
    size_bytes: number;
    status: AttachmentStatus;
    url: string;
};

