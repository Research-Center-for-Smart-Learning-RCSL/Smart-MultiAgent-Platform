/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CanvasObjectKind } from './CanvasObjectKind';
export type CanvasObjectOut = {
    id: string;
    canvas_id: string;
    kind: CanvasObjectKind;
    content: (string | null);
    minio_path: (string | null);
    position_x: number;
    position_y: number;
    width: number;
    height: number;
    z_index: number;
    style: Record<string, any>;
    created_by_user_id: (string | null);
    created_by_guest_id: (string | null);
    created_by_agent_id: (string | null);
    created_at: string;
    updated_at: string;
    image_url?: (string | null);
};

