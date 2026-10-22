import { simEmail, type Target } from '../config/target.ts'
import { derivePassword } from '../config/secrets.ts'
import { ApiClient, HttpError, tokenFromLink } from '../lib/http.ts'
import type { Logger, Redactor } from '../lib/log.ts'
import type { Scenario } from '../scenario/schema.ts'

export interface SimAccount {
  actor: string
  email: string
  password: string
  userId: string
}

interface UserSummary {
  id: string
  email: string
  display_name: string | null
  status: string
  email_verified: boolean
}

interface ActivationLinks {
  set_password_url: string
  verify_email_url: string
}

export interface ProvisionDeps {
  target: Target
  admin: ApiClient
  facilitator: ApiClient
  redactor: Redactor
  log: Logger
  secret: string
  /** Builds a client for a simulated account's own API calls (invite acceptance, profile). */
  clientFor: () => ApiClient
}

async function findUser(admin: ApiClient, email: string): Promise<UserSummary | null> {
  const rows = await admin.get<UserSummary[]>(`/api/admin/users?q=${encodeURIComponent(email)}&limit=50`)
  return rows.find((u) => u.email.toLowerCase() === email.toLowerCase()) ?? null
}

async function activate(admin: ApiClient, links: ActivationLinks, password: string, redactor: Redactor): Promise<void> {
  await admin.anonymous('POST', '/api/auth/reset-password', {
    token: tokenFromLink(links.set_password_url, redactor),
    new_password: password,
  })
  await admin.anonymous('POST', '/api/auth/verify-email', { token: tokenFromLink(links.verify_email_url, redactor) })
}

/**
 * Makes one simulated account exist, be active, and log in with the derived password.
 * Idempotent: an existing active account is only logged in to.
 */
async function ensureAccount(deps: ProvisionDeps, actor: string, n: number, displayName: string): Promise<{ account: SimAccount; client: ApiClient }> {
  const { admin, target, redactor, log } = deps
  const email = simEmail(target, n)
  const password = derivePassword(deps.secret, email)
  redactor.add(password)
  let user = await findUser(admin, email)
  if (!user) {
    const res = await admin.post<{ user: UserSummary; activation_links: ActivationLinks }>('/api/admin/users', {
      email,
      display_name: displayName,
    })
    user = res.user
    await activate(admin, res.activation_links, password, redactor)
    log.event('provision.created', { actor, email })
  } else if (user.status === 'pending' || !user.email_verified) {
    const links = await admin.post<ActivationLinks>(`/api/admin/users/${user.id}/activation-links`)
    await activate(admin, links, password, redactor)
    log.event('provision.activated', { actor, email })
  }
  const client = deps.clientFor()
  try {
    await client.login(email, password)
  } catch (err) {
    if (err instanceof HttpError && err.status === 401) {
      throw new Error(
        `${email} exists but the derived password does not log in. SMAP_SIM_SECRET probably changed since it was ` +
          'provisioned; use a new simEmailPrefix in the target file, or reset that account as an admin.',
      )
    }
    throw err
  }
  if ((user.display_name ?? '') !== displayName) {
    await client.patch('/api/auth/me', { display_name: displayName })
    log.event('provision.renamed', { actor, email, displayName })
  }
  return { account: { actor, email, password, userId: user.id }, client }
}

interface ProjectMember {
  user_id: string
  role: string
}

interface MemberGroup {
  id: string
  name: string
}

/** Accounts, project membership and the Member Group for every student actor (spec §6.3). */
export async function provision(deps: ProvisionDeps, scenario: Scenario): Promise<{ accounts: Map<string, SimAccount>; groupId: string | null }> {
  const { facilitator, target, log } = deps
  const accounts = new Map<string, SimAccount>()
  const members = await facilitator.get<ProjectMember[]>(`/api/projects/${target.projectId}/members?limit=500`)
  const memberIds = new Set(members.map((m) => m.user_id))

  for (const actor of scenario.actors.filter((a) => a.role === 'student')) {
    const n = actor.account as number
    const { account, client } = await ensureAccount(deps, actor.name, n, actor.displayName ?? `Sim ${n}`)
    accounts.set(actor.name, account)
    if (!memberIds.has(account.userId)) {
      const invite = await facilitator.post<{ accept_url: string | null }>(`/api/projects/${target.projectId}/invites`, {
        email: account.email,
        role: 'member',
      })
      if (!invite.accept_url) throw new Error(`invite for ${account.email} returned no accept link`)
      await client.post('/api/invites/accept-by-token', { token: tokenFromLink(invite.accept_url, deps.redactor) })
      memberIds.add(account.userId)
      log.event('provision.joined_project', { actor: actor.name })
    }
  }

  let groupId: string | null = null
  if (scenario.memberGroup) {
    const groups = await facilitator.get<MemberGroup[]>(`/api/projects/${target.projectId}/member-groups?limit=500`)
    let group = groups.find((g) => g.name === scenario.memberGroup?.name) ?? null
    if (!group) {
      group = await facilitator.post<MemberGroup>(`/api/projects/${target.projectId}/member-groups`, { name: scenario.memberGroup.name })
      log.event('provision.group_created', { group: group.name })
    }
    groupId = group.id
    const inGroup = new Set(
      (await facilitator.get<Array<{ user_id: string }>>(`/api/member-groups/${group.id}/members`)).map((m) => m.user_id),
    )
    for (const name of scenario.memberGroup.members) {
      const account = accounts.get(name)
      if (account && !inGroup.has(account.userId)) {
        await facilitator.post(`/api/member-groups/${group.id}/members`, { user_id: account.userId })
        log.event('provision.group_member_added', { actor: name, group: group.name })
      }
    }
  }
  return { accounts, groupId }
}
