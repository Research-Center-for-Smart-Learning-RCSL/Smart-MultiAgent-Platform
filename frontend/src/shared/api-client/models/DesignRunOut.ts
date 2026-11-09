/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DesignRunStatus } from './DesignRunStatus';
import type { DesignTargetOut } from './DesignTargetOut';
export type DesignRunOut = {
    activity_name: (string | null);
    activity_type_id: (string | null);
    applied_at: (string | null);
    created_at: string;
    design_agent_id: string;
    difficulty: (string | null);
    failure_reason: (string | null);
    finished_at: (string | null);
    form_notes: string;
    id: string;
    notes: (string | null);
    peer: DesignTargetOut;
    peer_error_pattern: (string | null);
    rounds: (number | null);
    status: DesignRunStatus;
    teacher: DesignTargetOut;
    tone: (string | null);
};

