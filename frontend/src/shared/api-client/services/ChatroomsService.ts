/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentRef } from '../models/AgentRef';
import type { ChatroomCreateIn } from '../models/ChatroomCreateIn';
import type { ChatroomOut } from '../models/ChatroomOut';
import type { ChatroomPatchIn } from '../models/ChatroomPatchIn';
import type { GuestLinkOut } from '../models/GuestLinkOut';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ChatroomsService {
    /**
     * Delete Chatroom
     * @returns void
     * @throws ApiError
     */
    public static deleteChatroomApiChatroomsChatroomIdDelete({
        chatroomId,
    }: {
        chatroomId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/chatrooms/{chatroom_id}',
            path: {
                'chatroom_id': chatroomId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Read Chatroom
     * @returns ChatroomOut Successful Response
     * @throws ApiError
     */
    public static readChatroomApiChatroomsChatroomIdGet({
        chatroomId,
    }: {
        chatroomId: string,
    }): CancelablePromise<ChatroomOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/chatrooms/{chatroom_id}',
            path: {
                'chatroom_id': chatroomId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Patch Chatroom
     * @returns ChatroomOut Successful Response
     * @throws ApiError
     */
    public static patchChatroomApiChatroomsChatroomIdPatch({
        chatroomId,
        ifMatch,
        requestBody,
    }: {
        chatroomId: string,
        ifMatch: string,
        requestBody: ChatroomPatchIn,
    }): CancelablePromise<ChatroomOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/chatrooms/{chatroom_id}',
            path: {
                'chatroom_id': chatroomId,
            },
            headers: {
                'If-Match': ifMatch,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Chatroom Agents
     * @returns AgentRef Successful Response
     * @throws ApiError
     */
    public static listChatroomAgentsApiChatroomsChatroomIdAgentsGet({
        chatroomId,
        limit = 100,
        offset,
    }: {
        chatroomId: string,
        /**
         * Max items to return
         */
        limit?: number,
        /**
         * Number of items to skip
         */
        offset?: number,
    }): CancelablePromise<Array<AgentRef>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/chatrooms/{chatroom_id}/agents',
            path: {
                'chatroom_id': chatroomId,
            },
            query: {
                'limit': limit,
                'offset': offset,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Add Chatroom Agent
     * @returns void
     * @throws ApiError
     */
    public static addChatroomAgentApiChatroomsChatroomIdAgentsPost({
        chatroomId,
        requestBody,
    }: {
        chatroomId: string,
        requestBody: AgentRef,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/chatrooms/{chatroom_id}/agents',
            path: {
                'chatroom_id': chatroomId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Remove Chatroom Agent
     * @returns void
     * @throws ApiError
     */
    public static removeChatroomAgentApiChatroomsChatroomIdAgentsAgentIdDelete({
        chatroomId,
        agentId,
    }: {
        chatroomId: string,
        agentId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/chatrooms/{chatroom_id}/agents/{agent_id}',
            path: {
                'chatroom_id': chatroomId,
                'agent_id': agentId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Force context compaction for active agents in this room (G.10)
     * Trigger an immediate compaction pass for the room.
     *
     * Records a one-shot intent flag (K.2): the next agent turn in this room
     * reads + clears it and forces a compaction pass before its provider call
     * (``turn_engine._consume_compact_flag``). Returns 202 so the frontend slash
     * command completes immediately.
     * @returns string Successful Response
     * @throws ApiError
     */
    public static compactChatroomApiChatroomsChatroomIdCompactPost({
        chatroomId,
    }: {
        chatroomId: string,
    }): CancelablePromise<Record<string, string>> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/chatrooms/{chatroom_id}/compact',
            path: {
                'chatroom_id': chatroomId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Read Guest Link
     * @returns GuestLinkOut Successful Response
     * @throws ApiError
     */
    public static readGuestLinkApiChatroomsChatroomIdGuestLinkGet({
        chatroomId,
    }: {
        chatroomId: string,
    }): CancelablePromise<GuestLinkOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/chatrooms/{chatroom_id}/guest-link',
            path: {
                'chatroom_id': chatroomId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Chatrooms
     * @returns ChatroomOut Successful Response
     * @throws ApiError
     */
    public static listChatroomsApiWorkspacesWorkspaceIdChatroomsGet({
        workspaceId,
        limit = 100,
        offset,
    }: {
        workspaceId: string,
        /**
         * Max items to return
         */
        limit?: number,
        /**
         * Number of items to skip
         */
        offset?: number,
    }): CancelablePromise<Array<ChatroomOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/workspaces/{workspace_id}/chatrooms',
            path: {
                'workspace_id': workspaceId,
            },
            query: {
                'limit': limit,
                'offset': offset,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Chatroom
     * @returns ChatroomOut Successful Response
     * @throws ApiError
     */
    public static createChatroomApiWorkspacesWorkspaceIdChatroomsPost({
        workspaceId,
        requestBody,
    }: {
        workspaceId: string,
        requestBody: ChatroomCreateIn,
    }): CancelablePromise<ChatroomOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/workspaces/{workspace_id}/chatrooms',
            path: {
                'workspace_id': workspaceId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
