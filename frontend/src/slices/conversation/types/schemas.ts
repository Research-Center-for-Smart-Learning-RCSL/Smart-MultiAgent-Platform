import { z } from 'zod'

export const chatroomCreateSchema = z.object({
  name: z.string().trim().min(1).max(200),
  allow_org_members: z.boolean().default(false),
  allow_project_members: z.boolean().default(true),
  allow_project_owners_only: z.boolean().default(false),
  allow_guest_links: z.boolean().default(false),
})

export type ChatroomCreateInput = z.infer<typeof chatroomCreateSchema>

export const workspaceCreateSchema = z.object({
  name: z.string().trim().min(1).max(200),
})

export type WorkspaceCreateInput = z.infer<typeof workspaceCreateSchema>
