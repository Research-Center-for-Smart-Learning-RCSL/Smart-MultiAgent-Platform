/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ActivityActivationOut } from '../models/ActivityActivationOut';
import type { ActivityActivationProgressOut } from '../models/ActivityActivationProgressOut';
import type { ActivityActivationStartIn } from '../models/ActivityActivationStartIn';
import type { ActivityGroupProposalIn } from '../models/ActivityGroupProposalIn';
import type { ActivityGroupProposalOut } from '../models/ActivityGroupProposalOut';
import type { ActivityGroupProposalsOut } from '../models/ActivityGroupProposalsOut';
import type { ActivityGroupVoteIn } from '../models/ActivityGroupVoteIn';
import type { ActivityPolicyPublicOut } from '../models/ActivityPolicyPublicOut';
import type { ActivitySessionCompletionIn } from '../models/ActivitySessionCompletionIn';
import type { ActivitySessionOpenIn } from '../models/ActivitySessionOpenIn';
import type { ActivitySessionOut } from '../models/ActivitySessionOut';
import type { ActivitySubmissionIn } from '../models/ActivitySubmissionIn';
import type { ActivitySubmissionOut } from '../models/ActivitySubmissionOut';
import type { ActivitySubmissionsPageOut } from '../models/ActivitySubmissionsPageOut';
import type { ActivityTypeIn } from '../models/ActivityTypeIn';
import type { ActivityTypeOptInIn } from '../models/ActivityTypeOptInIn';
import type { ActivityTypeOptInResultOut } from '../models/ActivityTypeOptInResultOut';
import type { ActivityTypeOut } from '../models/ActivityTypeOut';
import type { ActivityTypePublicOut } from '../models/ActivityTypePublicOut';
import type { ActivityTypeRegisteredOut } from '../models/ActivityTypeRegisteredOut';
import type { ActivityTypeUpdateIn } from '../models/ActivityTypeUpdateIn';
import type { ActivityValidatorOut } from '../models/ActivityValidatorOut';
import type { PlatformExampleOut } from '../models/PlatformExampleOut';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ActivitiesService {
    /**
     * Register Activity Type
     * @returns ActivityTypeRegisteredOut Successful Response
     * @throws ApiError
     */
    public static registerActivityTypeApiProjectsProjectIdActivityTypesPost({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: ActivityTypeIn,
    }): CancelablePromise<ActivityTypeRegisteredOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/projects/{project_id}/activity-types',
            path: {
                'project_id': projectId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * List Activity Types
     * Membership gate is unchanged — a non-owner member still legitimately
     * lists types (that is how a facilitator picks one to activate). Only
     * `validator_config` is owner-gated (R30.25): it may hold answer keys and,
     * once sealed validator credentials exist, secrets.
     * @returns ActivityTypeOut Successful Response
     * @throws ApiError
     */
    public static listActivityTypesApiProjectsProjectIdActivityTypesGet({
        projectId,
    }: {
        projectId: string,
    }): CancelablePromise<Array<ActivityTypeOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/projects/{project_id}/activity-types',
            path: {
                'project_id': projectId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Update Activity Type
     * @returns ActivityTypeOut Successful Response
     * @throws ApiError
     */
    public static updateActivityTypeApiProjectsProjectIdActivityTypesTypeIdPatch({
        projectId,
        typeId,
        requestBody,
    }: {
        projectId: string,
        typeId: string,
        requestBody: ActivityTypeUpdateIn,
    }): CancelablePromise<ActivityTypeOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/projects/{project_id}/activity-types/{type_id}',
            path: {
                'project_id': projectId,
                'type_id': typeId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Delete Activity Type
     * @returns void
     * @throws ApiError
     */
    public static deleteActivityTypeApiProjectsProjectIdActivityTypesTypeIdDelete({
        projectId,
        typeId,
    }: {
        projectId: string,
        typeId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/projects/{project_id}/activity-types/{type_id}',
            path: {
                'project_id': projectId,
                'type_id': typeId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * List Platform Activity Examples
     * The installed platform examples, with this project's enabled state.
     *
     * Project Owner rather than plain membership: the only thing this listing is for
     * is deciding what to enable, which is the owner's call ([R30.23]). It is also
     * why the catalogue being visible to every owner is acceptable — installed
     * examples are platform metadata, not another tenant's data (OQ-2).
     * @returns PlatformExampleOut Successful Response
     * @throws ApiError
     */
    public static listPlatformActivityExamplesApiProjectsProjectIdActivityExamplesGet({
        projectId,
    }: {
        projectId: string,
    }): CancelablePromise<Array<PlatformExampleOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/projects/{project_id}/activity-examples',
            path: {
                'project_id': projectId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Opt Project Into Activity Type
     * Enable a platform example for this project ([R30.33]).
     *
     * 200 with a body rather than the 204 this used to return: opting into a key
     * the project already owns is permitted but leaves two live types under one key
     * ([R30.02]), and the owner has to be told at the moment they do it.
     * @returns ActivityTypeOptInResultOut Successful Response
     * @throws ApiError
     */
    public static optProjectIntoActivityTypeApiProjectsProjectIdActivityTypeOptinsPost({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: ActivityTypeOptInIn,
    }): CancelablePromise<ActivityTypeOptInResultOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/projects/{project_id}/activity-type-optins',
            path: {
                'project_id': projectId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Opt Project Out Of Activity Type
     * Disable a platform example for this project, ending only its activations.
     *
     * Same post-commit ordering as ``delete_activity_type``: the opt-in removal and
     * every activation-end must be durable before any room is told its activation
     * ended.
     * @returns void
     * @throws ApiError
     */
    public static optProjectOutOfActivityTypeApiProjectsProjectIdActivityTypeOptinsTypeIdDelete({
        projectId,
        typeId,
    }: {
        projectId: string,
        typeId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/projects/{project_id}/activity-type-optins/{type_id}',
            path: {
                'project_id': projectId,
                'type_id': typeId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Get Room Activity Type
     * Room-scoped rendering-contract read (R30.26, Q-1): the recovery path
     * when the activation-started broadcast was missed, the store was reset, or
     * a future flow needs a type that is not the currently active one. Gated by
     * the room-access chain, not project membership, so a chatroom guest is a
     * full activity participant.
     * @returns ActivityTypePublicOut Successful Response
     * @throws ApiError
     */
    public static getRoomActivityTypeApiChatroomsChatroomIdActivityTypesTypeIdGet({
        chatroomId,
        typeId,
    }: {
        chatroomId: string,
        typeId: string,
    }): CancelablePromise<ActivityTypePublicOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/chatrooms/{chatroom_id}/activity-types/{type_id}',
            path: {
                'chatroom_id': chatroomId,
                'type_id': typeId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Start Activity Activation
     * @returns ActivityActivationOut Successful Response
     * @throws ApiError
     */
    public static startActivityActivationApiChatroomsChatroomIdActivityActivationsPost({
        chatroomId,
        requestBody,
    }: {
        chatroomId: string,
        requestBody: ActivityActivationStartIn,
    }): CancelablePromise<ActivityActivationOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/chatrooms/{chatroom_id}/activity-activations',
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
     * End Activity Activation
     * @returns ActivityActivationOut Successful Response
     * @throws ApiError
     */
    public static endActivityActivationApiChatroomsChatroomIdActivityActivationsActivationIdEndPatch({
        chatroomId,
        activationId,
    }: {
        chatroomId: string,
        activationId: string,
    }): CancelablePromise<ActivityActivationOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/chatrooms/{chatroom_id}/activity-activations/{activation_id}/end',
            path: {
                'chatroom_id': chatroomId,
                'activation_id': activationId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Get Active Activity Activation
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getActiveActivityActivationApiChatroomsChatroomIdActivityActivationsActiveGet({
        chatroomId,
    }: {
        chatroomId: string,
    }): CancelablePromise<(ActivityActivationOut | null)> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/chatrooms/{chatroom_id}/activity-activations/active',
            path: {
                'chatroom_id': chatroomId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Set Activity Session Completion
     * A participant declares themselves finished with the running activity, or
     * undoes it ([R30.22]).
     *
     * Keyed on the activation rather than on a session id: participants no longer
     * open sessions, so a client legitimately has no session id to send -- the
     * server resolves or creates the one for this round. ``ensure_can_send``
     * because this writes; the subject is forced to the caller inside the service
     * (the admin arm passes ``caller_user_id=None``, as the session open does).
     * @returns ActivitySessionOut Successful Response
     * @throws ApiError
     */
    public static setActivitySessionCompletionApiChatroomsChatroomIdActivityActivationsActivationIdCompletionPatch({
        chatroomId,
        activationId,
        requestBody,
    }: {
        chatroomId: string,
        activationId: string,
        requestBody: ActivitySessionCompletionIn,
    }): CancelablePromise<ActivitySessionOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/chatrooms/{chatroom_id}/activity-activations/{activation_id}/completion',
            path: {
                'chatroom_id': chatroomId,
                'activation_id': activationId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Get Activity Session Completion
     * The caller's own session for this round, or ``null`` if they have none.
     *
     * The read counterpart of the completion PATCH. Without it a participant who
     * reloads cannot know they had already declared themselves finished: they hold
     * no session id to ask with, and the panel would render the toggle in the wrong
     * state. Creates nothing — looking at the panel is not answering.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getActivitySessionCompletionApiChatroomsChatroomIdActivityActivationsActivationIdCompletionGet({
        chatroomId,
        activationId,
    }: {
        chatroomId: string,
        activationId: string,
    }): CancelablePromise<(ActivitySessionOut | null)> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/chatrooms/{chatroom_id}/activity-activations/{activation_id}/completion',
            path: {
                'chatroom_id': chatroomId,
                'activation_id': activationId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Get Activity Activation Progress
     * How many participants have declared themselves finished ([R30.22]).
     *
     * ``ensure_room_creator``, not the send floor: this is the facilitator's view
     * of the class, and a participant learning how many peers have finished is a
     * different decision nobody has made.
     * @returns ActivityActivationProgressOut Successful Response
     * @throws ApiError
     */
    public static getActivityActivationProgressApiChatroomsChatroomIdActivityActivationsActivationIdProgressGet({
        chatroomId,
        activationId,
    }: {
        chatroomId: string,
        activationId: string,
    }): CancelablePromise<ActivityActivationProgressOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/chatrooms/{chatroom_id}/activity-activations/{activation_id}/progress',
            path: {
                'chatroom_id': chatroomId,
                'activation_id': activationId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Open Activity Session
     * @returns ActivitySessionOut Successful Response
     * @throws ApiError
     */
    public static openActivitySessionApiChatroomsChatroomIdActivitySessionsPost({
        chatroomId,
        requestBody,
    }: {
        chatroomId: string,
        requestBody: ActivitySessionOpenIn,
    }): CancelablePromise<ActivitySessionOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/chatrooms/{chatroom_id}/activity-sessions',
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
     * Close Activity Session
     * @returns ActivitySessionOut Successful Response
     * @throws ApiError
     */
    public static closeActivitySessionApiChatroomsChatroomIdActivitySessionsSessionIdClosePatch({
        chatroomId,
        sessionId,
    }: {
        chatroomId: string,
        sessionId: string,
    }): CancelablePromise<ActivitySessionOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/chatrooms/{chatroom_id}/activity-sessions/{session_id}/close',
            path: {
                'chatroom_id': chatroomId,
                'session_id': sessionId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Submit Activity
     * @returns ActivitySubmissionOut Successful Response
     * @throws ApiError
     */
    public static submitActivityApiChatroomsChatroomIdActivitySubmissionsPost({
        chatroomId,
        requestBody,
    }: {
        chatroomId: string,
        requestBody: ActivitySubmissionIn,
    }): CancelablePromise<ActivitySubmissionOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/chatrooms/{chatroom_id}/activity-submissions',
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
     * List Activity Submissions
     * @returns ActivitySubmissionsPageOut Successful Response
     * @throws ApiError
     */
    public static listActivitySubmissionsApiChatroomsChatroomIdActivitySubmissionsGet({
        chatroomId,
        sessionId,
        subjectUserId,
        limit = 100,
        offset,
    }: {
        chatroomId: string,
        sessionId?: (string | null),
        subjectUserId?: (string | null),
        /**
         * Max items to return
         */
        limit?: number,
        /**
         * Number of items to skip
         */
        offset?: number,
    }): CancelablePromise<ActivitySubmissionsPageOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/chatrooms/{chatroom_id}/activity-submissions',
            path: {
                'chatroom_id': chatroomId,
            },
            query: {
                'session_id': sessionId,
                'subject_user_id': subjectUserId,
                'limit': limit,
                'offset': offset,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Create Activity Group Proposal
     * Propose this group's answer to the live round (AC-5).
     *
     * ``ensure_can_send``, not ``ensure_can_read``: proposing is the first half of
     * submitting, and a reader who may not answer may not start a vote that would
     * answer for them either. The group gates are the service's — this route knows
     * nothing about groups beyond forwarding the id the caller named.
     *
     * Creating one can also settle it, when the fraction over the pinned set rounds
     * down to the proposer's own approval. The post-commit fan-out is therefore the
     * vote route's, not a shorter version of it.
     * @returns ActivityGroupProposalOut Successful Response
     * @throws ApiError
     */
    public static createActivityGroupProposalApiChatroomsChatroomIdActivityProposalsPost({
        chatroomId,
        requestBody,
    }: {
        chatroomId: string,
        requestBody: ActivityGroupProposalIn,
    }): CancelablePromise<ActivityGroupProposalOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/chatrooms/{chatroom_id}/activity-proposals',
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
     * List Activity Group Proposals
     * The live proposals this caller may see for one round, and the groups they
     * may propose for (AC-12).
     *
     * Room access is necessary and not sufficient: the service narrows to the
     * caller's own bound groups, or to every bound group for the room creator. A
     * room member in no group sees an empty list rather than a 403 — there is
     * nothing being withheld from them, there is simply nothing of theirs.
     *
     * Both halves in one response because the participant panel needs both to
     * render anything at all, and two reads could disagree about which groups this
     * caller is in.
     * @returns ActivityGroupProposalsOut Successful Response
     * @throws ApiError
     */
    public static listActivityGroupProposalsApiChatroomsChatroomIdActivityProposalsGet({
        chatroomId,
        activationId,
    }: {
        chatroomId: string,
        activationId: string,
    }): CancelablePromise<ActivityGroupProposalsOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/chatrooms/{chatroom_id}/activity-proposals',
            path: {
                'chatroom_id': chatroomId,
            },
            query: {
                'activation_id': activationId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Vote On Activity Group Proposal
     * Record this caller's vote, and submit if it carries the proposal.
     *
     * Everything after the commit is the submit path's own post-commit fan-out,
     * reached with the submission an acceptance produced — so a group submission
     * reaches the room, the validation worker and the reactive rules by exactly the
     * routes an individual one does.
     * @returns ActivityGroupProposalOut Successful Response
     * @throws ApiError
     */
    public static voteOnActivityGroupProposalApiChatroomsChatroomIdActivityProposalsProposalIdVotesPost({
        chatroomId,
        proposalId,
        requestBody,
    }: {
        chatroomId: string,
        proposalId: string,
        requestBody: ActivityGroupVoteIn,
    }): CancelablePromise<ActivityGroupProposalOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/chatrooms/{chatroom_id}/activity-proposals/{proposal_id}/votes',
            path: {
                'chatroom_id': chatroomId,
                'proposal_id': proposalId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Withdraw Activity Group Proposal
     * @returns ActivityGroupProposalOut Successful Response
     * @throws ApiError
     */
    public static withdrawActivityGroupProposalApiChatroomsChatroomIdActivityProposalsProposalIdWithdrawPost({
        chatroomId,
        proposalId,
    }: {
        chatroomId: string,
        proposalId: string,
    }): CancelablePromise<ActivityGroupProposalOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/chatrooms/{chatroom_id}/activity-proposals/{proposal_id}/withdraw',
            path: {
                'chatroom_id': chatroomId,
                'proposal_id': proposalId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * Get Activity Group Proposal
     * @returns ActivityGroupProposalOut Successful Response
     * @throws ApiError
     */
    public static getActivityGroupProposalApiChatroomsChatroomIdActivityProposalsProposalIdGet({
        chatroomId,
        proposalId,
    }: {
        chatroomId: string,
        proposalId: string,
    }): CancelablePromise<ActivityGroupProposalOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/chatrooms/{chatroom_id}/activity-proposals/{proposal_id}',
            path: {
                'chatroom_id': chatroomId,
                'proposal_id': proposalId,
            },
            errors: {
                422: `Request Validation Problem`,
            },
        });
    }
    /**
     * List Activity Validators
     * List the registered first-party in-process validators (R30.24). Global and
     * process-scoped — availability never varies per project — so any authenticated
     * caller reads the same set the picker draws from. Exposes ids/titles only.
     * @returns ActivityValidatorOut Successful Response
     * @throws ApiError
     */
    public static listActivityValidatorsApiActivityValidatorsGet(): CancelablePromise<Array<ActivityValidatorOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/activity-validators',
        });
    }
    /**
     * Get Activity Policy Public
     * The policy in force, for the authoring form. Permissive when none is saved.
     * @returns ActivityPolicyPublicOut Successful Response
     * @throws ApiError
     */
    public static getActivityPolicyPublicApiActivityPolicyGet(): CancelablePromise<ActivityPolicyPublicOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/activity-policy',
        });
    }
}
