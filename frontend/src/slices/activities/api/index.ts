// Thin use-case wrappers over the generated ActivitiesService (R24.13). Auth,
// silent 401 refresh, and problem+json -> typed ApiError all come from the
// shared axios instrumentation the generated client calls into — the host
// mediates every plugin call through here, so a plugin never touches the
// session or network directly (R30.17).

import { ActivitiesService } from '@shared/api-client'
import type {
  ActivityPolicyPublicOut,
  ActivitySessionOpenIn,
  ActivitySessionOut,
  ActivityActivationOut,
  ActivityActivationStartIn,
  ActivitySubmissionIn,
  ActivitySubmissionOut,
  ActivitySubmissionsPageOut,
  ActivityTypeIn,
  ActivityTypeOut,
  ActivityTypePublicOut,
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

export async function registerActivityType(
  projectId: string,
  body: ActivityTypeIn,
): Promise<ActivityTypeOut> {
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

/** Enable a platform example for this project ([R30.33]). */
export async function optIntoActivityType(projectId: string, activityTypeId: string): Promise<void> {
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

export async function submitActivity(
  chatroomId: string,
  body: ActivitySubmissionIn,
): Promise<ActivitySubmissionOut> {
  return ActivitiesService.submitActivityApiChatroomsChatroomIdActivitySubmissionsPost({
    chatroomId,
    requestBody: body,
  })
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
