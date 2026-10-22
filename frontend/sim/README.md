# Classroom simulator

A command-line tool that lets one operator on one machine run a live multi-person classroom
against staging or the local compose stack. Each simulated participant is an isolated Chromium
context acting through the real UI, so the server sees separate users, a guest, typing, joins
and leaves exactly as it would in a classroom. Participants follow a scripted timeline, an LLM
persona, or both. After the run the tool collects the transcript, the agents' wake-up audit rows
and the group proposals into a run directory.

The design and its acceptance criteria are in
`docs/tasks/2026-10-22-classroom-simulator/spec.md`.

## Commands

All commands run from `frontend/`.

| Command | What it does |
|---|---|
| `pnpm sim dry-run --scenario <file>` | Validates a scenario and prints its schedule. Connects to nothing. |
| `pnpm sim provision --target <name> --scenario <file>` | Creates or reuses the simulated accounts, project memberships and Member Group. |
| `pnpm sim run --target <name> --scenario <file> [--room <id>] [--headed] [--facilitator human]` | Provisions, sets up the room, plays the scenario and writes evidence. |
| `pnpm sim collect --target <name> --room <id> --from <iso> --to <iso>` | Builds evidence for a room run by people, without a scenario. |
| `pnpm sim restore-limits --target <name>` | Restores an AUTH rate limit left raised by a killed run. |

Exit codes are 0 when no step failed (a `when` step that timed out under `onTimeout: "skip"` does not
count), 1 when a step failed or the run aborted, and 2 on a setup or usage error. The tool needs Node 22.18 or later, which runs the TypeScript sources
directly, and the Playwright Chromium already installed for the e2e suite
(`pnpm exec playwright install chromium`).

## Targets

A target is `sim/targets/<name>.json`. Copy `local.example.json` or `staging.example.json`
and fill in the ids; real target files are gitignored. There is no default target, so every
command that talks to a server names one.

| Field | Meaning |
|---|---|
| `baseUrl`, `allowedHost` | The host of `baseUrl` must equal `allowedHost`, or the tool refuses to run. A host other than localhost must use https. Redirects are refused. |
| `adminEmail` | A platform admin, used to create accounts, change the rate limit and read the audit log. |
| `facilitatorEmail` | The project owner who creates rooms and plays the teacher. |
| `projectId`, `workspaceId` | Where rooms are created and accounts become members. |
| `simEmailPrefix`, `simEmailDomain` | Simulated accounts are `<prefix>-<n>@<domain>`. The domain must pass the deployment's email-domain policy, and must be a reserved one (`example.com`, `example.org`, `example.net`, `*.test`, `*.invalid`, `*.example`), because whoever reads a simulated address's mail could reset that account. |
| `simDomainIsOperatorControlled` | Set `true` to allow any other domain, only when every mailbox in it is yours. |
| `allowRateLimitChange` | Must be `true` for the tool to raise the AUTH limit. Off in the staging example on purpose. |
| `authLimitPerMin` | The raised AUTH limit, at most 2000 per minute. |
| `ignoreHttpsErrors` | For a local stack behind a self-signed certificate only. |

## Secrets

Secrets come from the environment only and are never written to a target file, the state file
or a run directory. Every log line passes through a redactor that removes them, as well as any
bearer token or token field.

| Variable | Purpose |
|---|---|
| `SMAP_SIM_ADMIN_PASSWORD`, `SMAP_SIM_FACILITATOR_PASSWORD` | Prompted for (hidden) when unset. |
| `SMAP_SIM_SECRET` | At least 16 characters. Derives each simulated account's password, so anyone who holds it can log in as the simulated students. Keep it stable: a changed secret cannot log in to accounts created with the old one. Use a new `simEmailPrefix` instead, or reset those accounts as an admin. |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` | Needed only by scenarios with personas. |
| `SMAP_SIM_ANTHROPIC_MODEL`, `SMAP_SIM_OPENAI_MODEL` | Persona model when the scenario names none. Anthropic defaults to `claude-haiku-5-5`; OpenAI has no default. |
| `SMAP_SIM_OPENAI_BASE_URL` | An OpenAI-compatible endpoint other than `api.openai.com`. |

## The AUTH rate limit

Every participant logs in through the form, and the AUTH bucket allows 10 requests per minute
per IP for `/api/auth/*` and `/api/guest/*`. Ten browsers on one machine share one IP, so the
tool raises the bucket for the run through `PATCH /api/admin/rate-limits/auth` and restores the
previous value afterwards, also on Ctrl-C and on a failed step. The previous value is written to
`sim/.state/<target>.json` before the change, so a run killed outright is repaired by the next
command that names the same target, such as `restore-limits`. A machine that never runs the tool again leaves the
limit raised; the change is visible in the audit log as `admin.rate_limit_patched`. A restore that fails is
printed with the command to run, and the exit code is 2.

The restore is compare-and-set: the previous value is written back only while the live policy still
holds what this run set. If an admin changes the policy during a run, or a second simulator on another
machine raised it further, the tool leaves it and says so in the report. A second run against the same
target on the same machine is refused while the first is alive. Two operators on two machines should
not run against one target at the same time: the second one sees the first one's raised value as the
original, and the platform can end up with the raised value if the runs end in the wrong order.

The raise weakens brute-force protection for every user of the target for the length of the
run. Set `allowRateLimitChange` only for a deployment where that is acceptable.

## Scenarios

A scenario is a TypeScript module whose default export is `defineScenario({...})`
(`sim/scenario/schema.ts`). Loading one runs it, so a scenario file is trusted code, like a test spec:
run only scenarios you have read. It names the room (created per run, or an existing one), the Member
Group, the actors and the steps.

An actor has a `role` (`facilitator`, `student` or `guest`) and a `drive`: `script` follows
steps only; `llm` speaks from a persona only; `hybrid` does both, and a due script step cancels a
pending persona reply; `human` gets no browser, for a person taking part from their own machine.
`--facilitator human` turns the facilitator into a human whose steps print as timed cues.

A step has an actor, an action and when to fire: `at` (seconds, or `"m:ss"`, from the run start),
`when` (a message, activation or proposal condition), or both, in which case the condition is
armed at `at`. A condition that is not met within `timeoutSec` either skips the step or aborts the
run (`onTimeout`). Each actor's steps run in order.

| Action | Effect |
|---|---|
| `connect`, `leave` | Opens or closes the actor's browser. Actors without a `connect` step connect at the start. |
| `say` | Types at `typingCps` characters per second and sends. |
| `typePause` | Types part of a text, pauses, then sends, clears or keeps the draft. |
| `silent`, `wait` | Does nothing for a while (`silent` is recorded as an intended silence). |
| `openActivity`, `submit` | Opens the Activity panel; fills fields by key and submits. |
| `propose`, `vote`, `withdraw` | Group proposals for registered students. |
| `startActivity`, `endActivity`, `releaseObservation` | Facilitator controls. |
| `persona` | Turns a persona on or off. |

The shipped scenarios are `smoke-local` (the tool's own end-to-end check, no agents and no key),
`crat-ac20` (the AC-20 protocol of the CRAT phase 2 dossier) and `crat-llm-class` (three persona
students in the CRAT room).

## Local stack

1. Start the stack (`deploy/compose`, base plus override) and the Vite dev server.
2. As admin, install the `chinese-character-creativity` course and enable it for the project.
   The CRAT scenarios also need its room pack agents in the project.
3. Copy `local.example.json` to `local.json` and fill in the ids.
4. `pnpm sim run --target local --scenario sim/scenarios/smoke-local.ts --headed`.

## Personas

A persona reads the room through the facilitator's message API, waits a reading delay, and asks
its model for a JSON decision: `{"action":"say","text":"..."}`, `{"action":"type_pause","text":"..."}`
(starts typing, hesitates, then sends) or `{"action":"silent"}`; anything else counts as silence, and so
does text starting with `/`, which the composer would run as a command. It never speaks twice within `minIntervalSec`, stops after
`maxTurns`, and the scenario `budget` caps calls and output tokens for the whole run. Personas only
speak; proposals and votes stay scripted.

## Evidence

Each run writes `sim/runs/<UTC timestamp>-<scenario>/` (gitignored):

- `events.jsonl`: every step and persona decision with planned and actual times and outcome.
- `transcript.json`: the room's messages in the run window.
- `audit.json`: `wakeup.fired`, `wakeup.skipped` and `wakeup.failed` rows of the room's agents.
- `proposals.json`: group proposals seen during the run, merged by id.
- `report.md`: the timeline, wake counts per trigger, rate-limited skips, persona usage, the
  restored limit and the worker-log command to run by hand.
- `screens/`: each participant's page at the end of the run, and the failing participant's page when a
  step fails. Playwright traces are not
  recorded, because they capture tokens.

## Resource use

Each headless context takes roughly 150 to 250 MB (measured on Windows: five participants used
about 850 MB of Chromium private memory in total, plus about 130 MB for the Node process). Up to
ten participants is the supported range, so plan on 2.5 GB of free memory for a full class.
