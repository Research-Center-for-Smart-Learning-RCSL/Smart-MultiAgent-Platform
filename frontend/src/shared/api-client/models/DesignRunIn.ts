/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type DesignRunIn = {
    activity_type_id: string;
    design_agent_id: string;
    difficulty: 'introductory' | 'intermediate' | 'advanced';
    notes?: string;
    peer_agent_id: string;
    peer_error_pattern: 'none' | 'one_in_three';
    rounds: number;
    teacher_agent_id: string;
    tone: 'warm' | 'neutral' | 'concise';
};

