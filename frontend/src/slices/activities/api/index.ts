// Thin use-case wrappers over the generated ActivitiesService (R24.13). Auth,
// silent 401 refresh, and problem+json -> typed ApiError all come from the
// shared axios instrumentation the generated client calls into — the host
// mediates every plugin call through here, so a plugin never touches the
// session or network directly (R30.17).

import { ActivitiesService } from '@shared/api-client'
import type {
  ActivityActivationProgressOut,
  ActivityGroupProposalOut,
  ActivityGroupProposalsOut,
  ActivityPolicyPublicOut,
  ActivitySessionOpenIn,
  ActivitySessionOut,
  ActivityActivationOut,
  ActivityActivationStartIn,
  ActivitySubmissionIn,
  ActivitySubmissionOut,
  ActivitySubmissionsPageOut,
  ActivityTypeIn,
  ActivityTypeOptInResultOut,
  ActivityTypeOut,
  ActivityTypePublicOut,
  ActivityTypeRegisteredOut,
  ActivityTypeUpdateIn,
  ActivityValidatorOut,
  PlatformExampleOut,
} from '@shared/api-client'

export async function listActivityTypes(projectId: string): Promise<ActivityTypeOut[]> {
  return ActivitiesService.listActivityTypesApiProjectsProjectIdActivityTypesGet({ projectId })
}

/** Room-scoped rendering-contract read (Q-1): the recovery path when the
 *  activation-started broadcast was missed or the store was reset. */
export async function getRoomActivityType(
  chatroomId: string,
  typeId: string,
): Promise<ActivityTypePublicOut> {
  return ActivitiesService.getRoomActivityTypeApiChatroomsChatroomIdActivityTypesTypeIdGet({
    chatroomId,
    typeId,
  })
}

export async function listActivityValidators(): Promise<ActivityValidatorOut[]> {
  return ActivitiesService.listActivityValidatorsApiActivityValidatorsGet()
}

/** The platform governance policy, so the authoring form can pre-fill defaults
 *  and disable a locked switch (R30.29). The server re-checks on write; this only
 *  spares an owner from filling in a form that would be rejected. */
export async function getActivityPolicy(): Promise<ActivityPolicyPublicOut> {
  return ActivitiesService.getActivityPolicyPublicApiActivityPolicyGet()
}

/** Register a project-scoped type. The response is the created row plus
 *  `shadowed_by_platform` — the server's authoritative answer to whether this
 *  key now names two live types in the project's usable set ([R30.02]). */
export async function registerActivityType(
  projectId: string,
  body: ActivityTypeIn,
): Promise<ActivityTypeRegisteredOut> {
  return ActivitiesService.registerActivityTypeApiProjectsProjectIdActivityTypesPost({
    projectId,
    requestBody: body,
  })
}

export async function updateActivityType(
  projectId: string,
  typeId: string,
  body: ActivityTypeUpdateIn,
): Promise<ActivityTypeOut> {
  return ActivitiesService.updateActivityTypeApiProjectsProjectIdActivityTypesTypeIdPatch({
    projectId,
    typeId,
    requestBody: body,
  })
}

export async function deleteActivityType(projectId: string, typeId: string): Promise<void> {
  return ActivitiesService.deleteActivityTypeApiProjectsProjectIdActivityTypesTypeIdDelete({
    projectId,
    typeId,
  })
}

/** The installed platform examples plus this project's enabled state ([R30.32]).
 *  Project Owner only — the server gates it, this is just the call. */
export async function listPlatformExamples(projectId: string): Promise<PlatformExampleOut[]> {
  return ActivitiesService.listPlatformActivityExamplesApiProjectsProjectIdActivityExamplesGet({
    projectId,
  })
}

/** Enable a platform example for this project ([R30.33]). The result reports
 *  whether the project already owns a live type under the same key, which leaves
 *  two types under one key in its usable set ([R30.02]). */
export async function optIntoActivityType(
  projectId: string,
  activityTypeId: string,
): Promise<ActivityTypeOptInResultOut> {
  return ActivitiesService.optProjectIntoActivityTypeApiProjectsProjectIdActivityTypeOptinsPost({
    projectId,
    requestBody: { activity_type_id: activityTypeId },
  })
}

/** Disable it again. Ends this project's activations for the type and closes its
 *  open sessions — no other project is affected. */
export async function optOutOfActivityType(projectId: string, typeId: string): Promise<void> {
  return ActivitiesService.optProjectOutOfActivityTypeApiProjectsProjectIdActivityTypeOptinsTypeIdDelete(
    { projectId, typeId },
  )
}

export async function startActivation(
  chatroomId: string,
  body: ActivityActivationStartIn,
): Promise<ActivityActivationOut> {
  return ActivitiesService.startActivityActivationApiChatroomsChatroomIdActivityActivationsPost({
    chatroomId,
    requestBody: body,
  })
}

export async function endActivation(
  chatroomId: string,
  activationId: string,
): Promise<ActivityActivationOut> {
  return ActivitiesService.endActivityActivationApiChatroomsChatroomIdActivityActivationsActivationIdEndPatch({
    chatroomId,
    activationId,
  })
}

export async function getActiveActivation(chatroomId: string): Promise<ActivityActivationOut | null> {
  return ActivitiesService.getActiveActivityActivationApiChatroomsChatroomIdActivityActivationsActiveGet({
    chatroomId,
  })
}

export async function openActivitySession(
  chatroomId: string,
  body: ActivitySessionOpenIn,
): Promise<ActivitySessionOut> {
  return ActivitiesService.openActivitySessionApiChatroomsChatroomIdActivitySessionsPost({
    chatroomId,
    requestBody: body,
  })
}

export async function closeActivitySession(
  chatroomId: string,
  sessionId: string,
): Promise<ActivitySessionOut> {
  return ActivitiesService.closeActivitySessionApiChatroomsChatroomIdActivitySessionsSessionIdClosePatch(
    { chatroomId, sessionId },
  )
}

/** Declare the caller finished with the running activity, or undo it ([R30.22]).
 *
 *  Keyed on the activation, not on a session id: participants no longer open
 *  sessions, so the client has none to send and the server resolves or creates
 *  the one for this round. */
export async function setActivationCompletion(
  chatroomId: string,
  activationId: string,
  completed: boolean,
): Promise<ActivitySessionOut> {
  return ActivitiesService.setActivitySessionCompletionApiChatroomsChatroomIdActivityActivationsActivationIdCompletionPatch(
    { chatroomId, activationId, requestBody: { completed } },
  )
}

/** The caller's own session for this round, or `null` if they have none.
 *
 *  How a reloading participant learns they had already declared themselves
 *  finished: the client holds no session id, so there is nothing else to ask
 *  with. Creates nothing. */
export async function getOwnRoundSession(
  chatroomId: string,
  activationId: string,
): Promise<ActivitySessionOut | null> {
  return ActivitiesService.getActivitySessionCompletionApiChatroomsChatroomIdActivityActivationsActivationIdCompletionGet(
    { chatroomId, activationId },
  )
}

/** How many participants have declared themselves finished ([R30.22]).
 *  Room-creator only — a 403 here is the expected answer for everyone else, so
 *  the caller must not surface it as a failure on the participant surface. */
export async function getActivationProgress(
  chatroomId: string,
  activationId: string,
): Promise<ActivityActivationProgressOut> {
  return ActivitiesService.getActivityActivationProgressApiChatroomsChatroomIdActivityActivationsActivationIdProgressGet(
    { chatroomId, activationId },
  )
}

export async function submitActivity(
  chatroomId: string,
  body: ActivitySubmissionIn,
): Promise<ActivitySubmissionOut> {
  return ActivitiesService.submitActivityApiChatroomsChatroomIdActivitySubmissionsPost({
    chatroomId,
    requestBody: body,
  })
}

/** One round's group state for the caller ([R30.41], [R30.42]).
 *
 *  Two answers in one response on purpose: the proposals this caller may read,
 *  and the groups they may propose for. `eligible_groups` being empty is also
 *  the panel's signal that this caller submits individually — the server has
 *  already applied the three gates a proposal would run, so the client never
 *  decides eligibility itself. */
export async function listGroupProposals(
  chatroomId: string,
  activationId: string,
): Promise<ActivityGroupProposalsOut> {
  return ActivitiesService.listActivityGroupProposalsApiChatroomsChatroomIdActivityProposalsGet({
    chatroomId,
    activationId,
  })
}

/** Open a proposal for one group's answer. The server refuses a payload that
 *  does not already satisfy the type schema, so a proposal nobody could accept
 *  fails here rather than after three people have voted for it. */
export async function createGroupProposal(
  chatroomId: string,
  body: { activity_type_id: string; member_group_id: string; payload: Record<string, unknown> },
): Promise<ActivityGroupProposalOut> {
  return ActivitiesService.createActivityGroupProposalApiChatroomsChatroomIdActivityProposalsPost({
    chatroomId,
    requestBody: body,
  })
}

/** Record this caller's vote. The response is the whole tally after it, which
 *  may already be `accepted` — the vote that reaches the threshold submits. */
export async function voteOnGroupProposal(
  chatroomId: string,
  proposalId: string,
  approve: boolean,
): Promise<ActivityGroupProposalOut> {
  return ActivitiesService.voteOnActivityGroupProposalApiChatroomsChatroomIdActivityProposalsProposalIdVotesPost(
    { chatroomId, proposalId, requestBody: { approve } },
  )
}

export async function withdrawGroupProposal(
  chatroomId: string,
  proposalId: string,
): Promise<ActivityGroupProposalOut> {
  return ActivitiesService.withdrawActivityGroupProposalApiChatroomsChatroomIdActivityProposalsProposalIdWithdrawPost(
    { chatroomId, proposalId },
  )
}

export interface ListSubmissionsParams {
  sessionId?: string | null
  subjectUserId?: string | null
  limit?: number
  offset?: number
}

export async function listActivitySubmissions(
  chatroomId: string,
  params: ListSubmissionsParams = {},
): Promise<ActivitySubmissionsPageOut> {
  return ActivitiesService.listActivitySubmissionsApiChatroomsChatroomIdActivitySubmissionsGet({
    chatroomId,
    ...params,
  })
}
