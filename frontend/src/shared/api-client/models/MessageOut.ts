/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AttachmentOut } from './AttachmentOut';
export type MessageOut = {
    attachments?: Array<AttachmentOut>;
    chatroom_id: string;
    content_md: string;
    created_at: (string | null);
    deleted_at: (string | null);
    edited_at: (string | null);
    id: string;
    metadata: Record<string, any>;
    sender_id: (string | null);
    sender_type: string;
    version: number;
};

