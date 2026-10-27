import { z } from 'zod'

export const chatroomCreateSchema = z.object({
  name: z.string().trim().min(1).max(200),
  allow_org_members: z.boolean().default(false),
  allow_project_members: z.boolean().default(true),
  allow_project_owners_only: z.boolean().default(false),
  allow_guest_links: z.boolean().default(false),
  // R13.04 forbids this together with allow_project_members; the create form
  // offers the tiers as a single choice, so the pair cannot both be true here.
  allow_member_groups: z.boolean().default(false),
})

export type ChatroomCreateInput = z.infer<typeof chatroomCreateSchema>

export const workspaceCreateSchema = z.object({
  name: z.string().trim().min(1).max(200),
})

export type WorkspaceCreateInput = z.infer<typeof workspaceCreateSchema>

// Class pacing bounds mirror the routes' Pydantic fields ([R15.27], [R30.46]).
export const classStartMinParticipantsSchema = z.number().int().min(1).max(100)
export const classStartMaxWaitSchema = z.number().int().min(1).max(60)
export const roomTimeLimitMinutesSchema = z.number().int().min(1).max(180)
