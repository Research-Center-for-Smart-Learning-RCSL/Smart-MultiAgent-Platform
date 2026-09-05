/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AttachmentOut } from './AttachmentOut';
import type { SenderType } from './SenderType';
export type MessageOut = {
    id: string;
    chatroom_id: string;
    sender_type: SenderType;
    sender_id: (string | null);
    content_md: string;
    metadata: Record<string, any>;
    version: number;
    created_at: (string | null);
    edited_at: (string | null);
    deleted_at: (string | null);
    attachments?: Array<AttachmentOut>;
};

