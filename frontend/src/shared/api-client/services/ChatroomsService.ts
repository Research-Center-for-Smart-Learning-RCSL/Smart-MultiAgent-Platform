/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentActivityControlIn } from '../models/AgentActivityControlIn';
import type { AgentDraftAccessIn } from '../models/AgentDraftAccessIn';
import type { AgentRef } from '../models/AgentRef';
import type { AgentRolePatchIn } from '../models/AgentRolePatchIn';
import type { ApprovalWithVotesOut } from '../models/ApprovalWithVotesOut';
import type { ChatroomCreateIn } from '../models/ChatroomCreateIn';
import type { ChatroomMemberGroupsIn } from '../models/ChatroomMemberGroupsIn';
import type { ChatroomMemberGroupsOut } from '../models/ChatroomMemberGroupsOut';
import type { ChatroomMemberOut } from '../models/ChatroomMemberOut';
import type { ChatroomOut } from '../models/ChatroomOut';
import type { ChatroomPatchIn } from '../models/ChatroomPatchIn';
import type { GuestLinkOut } from '../models/GuestLinkOut';
import type { PresenceOut } from '../models/PresenceOut';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ChatroomsService {
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
                422: `Request Validation Problem`,
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
                422: `Request Validation Problem`,
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
                422: `Request Validation Problem`,
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
                422: `Request Validation Problem`,
            },
        });
    }
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
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * List Chatroom Member Groups
     * This room's live Member Group bindings (R13.29).
     *
     * A binding whose group was since deleted is omitted. The stored row is left
     * alone — the ACL already ignores it, and the repository deliberately does not
     * read tenancy's `deleted_at` — but it must not be *reported*, because the
     * settings UI sends this list straight back on the next edit and the PUT
     * refuses a deleted id. Reading raw rows here wedged the picker permanently.
     * @returns ChatroomMemberGroupsOut Successful Response
     * @throws ApiError
     */
    public static listChatroomMemberGroupsApiChatroomsChatroomIdMemberGroupsGet({
        chatroomId,
    }: {
        chatroomId: string,
    }): CancelablePromise<ChatroomMemberGroupsOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/chatrooms/{chatroom_id}/member-groups',
            path: {
                'chatroom_id': chatroomId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Set Chatroom Member Groups
     * Replace this room's Member Group bindings (R13.29).
     *
     * SEC: every id is checked to belong to **this room's** project before it is
     * written. Without that, an owner of project A could bind a group from project B
     * to a room in A and hand B's members a room they have no standing in — a
     * cross-project grant assembled entirely out of ids the caller is allowed to
     * know. The check reads the group rows rather than trusting the request.
     * @returns ChatroomMemberGroupsOut Successful Response
     * @throws ApiError
     */
    public static setChatroomMemberGroupsApiChatroomsChatroomIdMemberGroupsPut({
        chatroomId,
        requestBody,
    }: {
        chatroomId: string,
        requestBody: ChatroomMemberGroupsIn,
    }): CancelablePromise<ChatroomMemberGroupsOut> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/chatrooms/{chatroom_id}/member-groups',
            path: {
                'chatroom_id': chatroomId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
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
                422: `Request Validation Problem`,
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
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Patch Chatroom Agent Role
     * @returns void
     * @throws ApiError
     */
    public static patchChatroomAgentRoleApiChatroomsChatroomIdAgentsAgentIdPatch({
        chatroomId,
        agentId,
        requestBody,
    }: {
        chatroomId: string,
        agentId: string,
        requestBody: AgentRolePatchIn,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/chatrooms/{chatroom_id}/agents/{agent_id}',
            path: {
                'chatroom_id': chatroomId,
                'agent_id': agentId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
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
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Patch Chatroom Agent Activity Control
     * Delegate activity start/end authority in this room to a bound agent ([R30.37]).
     *
     * ``ensure_room_creator``, matching every other authority decision about this
     * room's bindings — and matching the gate on starting a round itself, which is
     * the authority being handed out. Nobody who cannot start an activity may grant
     * the power to.
     *
     * Every type id is resolved for the room's own project before anything is
     * written. That check has to live here: the conversation context stores the
     * allowlist but cannot see an activity type ([R30.05]), so the route is the only
     * layer that can perform it — the same shape as ``_assert_mcp_binding_in_project``
     * in ``activities.py``. Resolving before writing is what keeps a cross-project or
     * deleted id a 422 rather than a stored id that quietly resolves to nothing later.
     * @returns void
     * @throws ApiError
     */
    public static patchChatroomAgentActivityControlApiChatroomsChatroomIdAgentsAgentIdActivityControlPatch({
        chatroomId,
        agentId,
        requestBody,
    }: {
        chatroomId: string,
        agentId: string,
        requestBody: AgentActivityControlIn,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/chatrooms/{chatroom_id}/agents/{agent_id}/activity-control',
            path: {
                'chatroom_id': chatroomId,
                'agent_id': agentId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Patch Chatroom Agent Draft Access
     * Let one bound agent read this room's unsent text ([R32.03]).
     *
     * ``ensure_room_creator``, matching every other authority decision about this
     * room's bindings. It is the strictest gate available and this is the surface that
     * most deserves it: what is being handed out is the ability to read text the people
     * in this room have not chosen to send.
     *
     * **Its own route rather than a field on the role patch**, for the reason
     * ``activity-control`` is: a different authority with a different meaning, so the
     * audit trail carries one action per decision and a role change cannot silently
     * carry a grant along with it.
     *
     * Unlike ``activity-control`` there is nothing to validate before writing — no
     * allowlist, no cross-context resolution — because the read-time gates in
     * ``draft_tools`` are what bound this authority ([R32.04]). A grant is therefore
     * exactly as narrow as the room's own activity types already are.
     * @returns void
     * @throws ApiError
     */
    public static patchChatroomAgentDraftAccessApiChatroomsChatroomIdAgentsAgentIdDraftAccessPatch({
        chatroomId,
        agentId,
        requestBody,
    }: {
        chatroomId: string,
        agentId: string,
        requestBody: AgentDraftAccessIn,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/chatrooms/{chatroom_id}/agents/{agent_id}/draft-access',
            path: {
                'chatroom_id': chatroomId,
                'agent_id': agentId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * List Chatroom Members
     * Resolve human participants to display names so the client can label
     * message authors (REST history + live WS messages share one map).
     *
     * Only ``user_id`` + ``display_name`` is returned — never email — so a room
     * member (including a guest) cannot harvest other participants' login
     * identifiers. The id set is the union of distinct human message authors and
     * enrolled guests; a guest's per-room display name takes precedence over their
     * account display name. Names left unset resolve to ``null`` and the client
     * falls back to a short id.
     * @returns ChatroomMemberOut Successful Response
     * @throws ApiError
     */
    public static listChatroomMembersApiChatroomsChatroomIdMembersGet({
        chatroomId,
    }: {
        chatroomId: string,
    }): CancelablePromise<Array<ChatroomMemberOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/chatrooms/{chatroom_id}/members',
            path: {
                'chatroom_id': chatroomId,
            },
            errors: {
                422: `Request Validation Problem`,
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
                422: `Request Validation Problem`,
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
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Snapshot of users currently present via WebSocket
     * @returns PresenceOut Successful Response
     * @throws ApiError
     */
    public static getChatroomPresenceApiChatroomsChatroomIdPresenceGet({
        chatroomId,
    }: {
        chatroomId: string,
    }): CancelablePromise<PresenceOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/chatrooms/{chatroom_id}/presence',
            path: {
                'chatroom_id': chatroomId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * List approval gates raised in a chatroom
     * Room-scoped read side for F-13: the connect-time client reconcile fetches
     * this list to discover an approval gate whose `approval.requested` WS frame
     * was missed while disconnected. Gated the same way as every other room read
     * (`resolve_room_access` + `ensure_can_read`), not by project membership --
     * the room is the resource here, and a platform admin passes `ensure_can_read`
     * the same way every other room read already does. Rows created before the
     * `chatroom_id` column existed are simply absent, not an error (Q-4 of the
     * task dossier: no backfill).
     * @returns ApprovalWithVotesOut Successful Response
     * @throws ApiError
     */
    public static listChatroomApprovalsApiChatroomsChatroomIdApprovalsGet({
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
    }): CancelablePromise<Array<ApprovalWithVotesOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/chatrooms/{chatroom_id}/approvals',
            path: {
                'chatroom_id': chatroomId,
            },
            query: {
                'limit': limit,
                'offset': offset,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
}
