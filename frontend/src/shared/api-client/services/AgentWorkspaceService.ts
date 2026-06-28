/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Body_upload_workspace_file_api_agents__agent_id__workspace_files_post } from '../models/Body_upload_workspace_file_api_agents__agent_id__workspace_files_post';
import type { WorkspaceFileOut } from '../models/WorkspaceFileOut';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class AgentWorkspaceService {
    /**
     * List Workspace Files
     * @returns WorkspaceFileOut Successful Response
     * @throws ApiError
     */
    public static listWorkspaceFilesApiAgentsAgentIdWorkspaceFilesGet({
        agentId,
    }: {
        agentId: string,
    }): CancelablePromise<Array<WorkspaceFileOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/agents/{agent_id}/workspace-files',
            path: {
                'agent_id': agentId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Upload Workspace File
     * @returns WorkspaceFileOut Successful Response
     * @throws ApiError
     */
    public static uploadWorkspaceFileApiAgentsAgentIdWorkspaceFilesPost({
        agentId,
        formData,
    }: {
        agentId: string,
        formData: Body_upload_workspace_file_api_agents__agent_id__workspace_files_post,
    }): CancelablePromise<WorkspaceFileOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/agents/{agent_id}/workspace-files',
            path: {
                'agent_id': agentId,
            },
            formData: formData,
            mediaType: 'multipart/form-data',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Workspace File
     * @returns void
     * @throws ApiError
     */
    public static deleteWorkspaceFileApiAgentsAgentIdWorkspaceFilesFileIdDelete({
        agentId,
        fileId,
    }: {
        agentId: string,
        fileId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/agents/{agent_id}/workspace-files/{file_id}',
            path: {
                'agent_id': agentId,
                'file_id': fileId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
