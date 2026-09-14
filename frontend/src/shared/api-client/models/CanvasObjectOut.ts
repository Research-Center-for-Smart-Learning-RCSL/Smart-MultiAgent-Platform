/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CanvasObjectKind } from './CanvasObjectKind';
export type CanvasObjectOut = {
    canvas_id: string;
    content: (string | null);
    created_at: string;
    created_by_agent_id: (string | null);
    created_by_guest_id: (string | null);
    created_by_user_id: (string | null);
    height: number;
    id: string;
    image_url?: (string | null);
    kind: CanvasObjectKind;
    minio_path: (string | null);
    position_x: number;
    position_y: number;
    style: Record<string, any>;
    updated_at: string;
    width: number;
    z_index: number;
};

