# Stokowski → Cadence migration

This guide walks a repo currently running [Stokowski][stokowski] over to
the Cadence Claude Code plugin. The behaviour is the same end to end —
issues flow through a state machine, agents do the work, humans approve
at gates, PRs land. What changes is the runtime: instead of a per-project
Python daemon, Cadence is a packaged plugin that runs one tick at a time
under `/schedule` (or `/loop`).

[stokowski]: https://github.com/Sugar-Coffee/stokowski

---

## Why migrate

| Stokowski (Python daemon)                | Cadence (Claude Code plugin)             |
|------------------------------------------|------------------------------------------|
| Per-project install, custom Python env   | One plugin install, declarative YAML     |
| Long-running supervisor process          | Stateless ticks fired by `/schedule` or `/loop` |
| Workflow state lives in process memory + Linear | Linear is the sole source of workflow state |
| Stage prompts maintained as plain .md    | Subagents (`.md` + frontmatter)          |
| Stokowski writes comments / state itself | Bootstrap writes all of Linear; subagents return strings |
| Many internal states can share one Linear column | 1:1 — each workflow stage is a distinct Linear column |

If you're happy with Stokowski today, you don't have to migrate. Cadence
is the right choice when you want to:

- Stop maintaining a per-project Python install.
- Run the workflow from anywhere (`/schedule` is fully remote).
- Reuse the same workflow across multiple repos by installing one plugin.

---

## Step-by-step

### 1. Restructure your Linear board

**This is the most invasive change.** Stokowski lets multiple internal
states share one Linear column (e.g. `plan` and `implement` both map to
"In Progress"). Cadence forbids that — every workflow stage needs its own
Linear state, and every `linear_state` value in `workflow.yaml` must be
unique across the whole config.

Audit your current Stokowski `workflow.yaml`. For every internal state
that maps to a shared Linear state, add a new Linear column. Typical
result for a `plan → implement → review → done` workflow:

| Workflow state | New Linear column      |
|----------------|------------------------|
| (pickup)       | Todo                   |
| plan           | Planning               |
| implement      | Implementing           |
| review (waiting) | In Review            |
| review (approved) | Approved             |
| review (rework)  | Needs Rework         |
| done           | Done                   |

Create the missing columns in Linear before doing anything else. Issues
mid-flight should be moved manually to the column matching their current
internal state.

### 2. Rewrite `workflow.yaml`

Cadence schema (see `templates/workflow.example.yaml` for the canonical
shape):

```yaml
linear:
  team: ENG
  project_slug: abc123
  pickup_state: "Todo"

label:
  cadence_active: "cadence-active"
  cadence_needs_human: "cadence-needs-human"

limits:
  max_attempts_per_issue: 3
  # stale_after_minutes: 30   # optional

entry: plan

states:
  plan:
    type: agent
    subagent: planner
    linear_state: "Planning"
    next: implement

  implement:
    type: agent
    subagent: implementer
    linear_state: "Implementing"
    next: review

  review:
    type: gate
    linear_state: "In Review"
    approved_linear_state: "Approved"
    rework_linear_state: "Needs Rework"
    on_approve: done
    on_rework: implement
    max_rework: 2

  done:
    type: terminal
    linear_state: "Done"
```

Key renames and removals from the Stokowski schema:

- **`tracker:` → `linear:`** at the top level.
- **`transitions:`** block is gone. Agent states put their successor in a
  flat `next:` field. Gate states use `on_approve:` and `on_rework:`.
- **`linear_states:`** role mapping is gone. Each state declares its own
  `linear_state` directly.
- Drop entirely: `polling`, `workspace`, `hooks`, `claude`, `agent`,
  `server`. Cadence doesn't use them — these were Stokowski-daemon
  concerns. Tool restrictions, model selection, and lifecycle behaviour
  now live in each subagent's frontmatter.
- `pickup_state` moves from being implicit ("starting state of the
  workflow") to being an explicit Linear column where new issues land
  before the first fire moves them to the entry state.

### 3. Convert prompt files to subagents

Each Stokowski stage had a plain `.md` prompt file (e.g.
`prompts/plan.md`). Convert each to a Claude Code subagent under
`.claude/agents/` with YAML frontmatter:

```markdown
---
name: planner
description: Breaks down a Linear issue into a concrete implementation plan. Returns a Markdown plan summary string.
model: opus
tools: [Read, Grep, Glob, WebFetch, Bash]
---

(the prompt body, slightly adapted — see below)
```

Adaptations to the body:

- **Drop any "post your own Workpad" / "comment on the Linear issue"
  instructions.** Cadence's bootstrap is the sole Linear writer. The
  subagent's job is to return a Markdown string; the bootstrap posts it
  as a comment after the auto-generated state-tracking comment.
- **Drop any "move the Linear state when done" instructions.** Same
  reason — the bootstrap handles state moves.
- **Keep** all role-specific guidance: how to investigate, what files to
  read, how to verify, what to include in the returned summary.
- **Keep** the "headless, no questions" rules. Move generic ones into
  `.claude/prompts/global.md` if they apply to every subagent.

If you used Stokowski's three-layer prompt assembly (global + stage +
auto-injected lifecycle), the mapping is direct:

| Stokowski layer       | Cadence equivalent                              |
|-----------------------|-------------------------------------------------|
| Global prompt         | `.claude/prompts/global.md`                     |
| Stage prompt          | `.claude/agents/<name>.md` body                 |
| Auto-injected lifecycle | Lifecycle Context block (built by bootstrap)  |

### 4. Migrate the global prompt

Move `prompts/global.md` (or wherever Stokowski looked for it) to
`.claude/prompts/global.md`. Drop the Workpad instruction. Keep
"headless, no interactive commands, no sudo" rules. Add any repo
conventions you want every subagent to follow.

### 5. Existing tracking comments survive

Cadence's bootstrap parses both `cadence:` and `stokowski:` comment
prefixes, treating the legacy fields `run` and `timestamp` as the new
`attempt` and `started_at`. **You do not need to rewrite existing
comments** — attempt counts, failure records, gate history, all of it
flows across the migration boundary intact.

#### Worked example

Take issue ENG-456 which was already mid-flight on Stokowski when the
migration happened. Its Linear comment history might look like this
before, during, and after the cutover:

```
─── before migration ────────────────────────────────────────────────────

[Linear comment posted by Stokowski daemon, 2025-12-01 14:00 UTC]
<!-- stokowski:state {"state":"plan","run":1,"timestamp":"2025-12-01T14:00:00Z"} -->
**[Stokowski]** Entering state: **plan** (run 1)

[Linear comment posted by Stokowski daemon, 2025-12-01 14:18 UTC]
Plan complete. Will implement: refactor PaymentService to extract
RetryPolicy, add unit tests, update the integration test for the 5xx
backoff path. ETA ~4 hours.

[Linear comment posted by Stokowski daemon, 2025-12-01 14:19 UTC]
<!-- stokowski:state {"state":"implement","run":1,"timestamp":"2025-12-01T14:19:12Z"} -->
**[Stokowski]** Entering state: **implement** (run 1)

[Linear comment posted by Stokowski daemon, 2025-12-01 15:42 UTC]
<!-- stokowski:state {"state":"implement","run":1,"status":"failed","error":"gh pr create: rate limit exceeded"} -->
**[Stokowski]** Subagent failed at run 1: gh pr create: rate limit exceeded

─── migration cutover ──────────────────────────────────────────────────

(Stokowski daemon stopped. Linear board restructured to add separate
"Planning" / "Implementing" / "In Review" / "Approved" / "Needs Rework"
columns. ENG-456 is left sitting in "Implementing". Cadence /schedule
routine starts.)

─── after migration ────────────────────────────────────────────────────

[Linear comment posted by Cadence bootstrap, 2026-05-12 09:15 UTC]
<!-- cadence:state {"state":"implement","attempt":2,"started_at":"2026-05-12T09:15:00Z"} -->
**[Cadence]** Entering state: **implement** (attempt 2)
```

Two things to notice:

1. **Attempt counting crossed the boundary.** Cadence picked up
   ENG-456, scanned its tracking comments, found one legacy
   `stokowski:state` attempt marker for the `implement` state (the
   2025-12-01 14:19 comment), treated it as `attempt: 1`, and emitted
   its own marker as `attempt: 2`. The failure record at 15:42 was
   correctly ignored (it has `status: "failed"`, so it's not an attempt
   marker). If `limits.max_attempts_per_issue: 3`, ENG-456 has one
   retry left before escalation.

2. **The legacy comments stay put.** Cadence never rewrites or deletes
   them. Mixed prefixes (`stokowski:` and `cadence:`) coexist on the
   same issue indefinitely. The audit trail reads top to bottom across
   both eras.

The same survives for gates: a `<!-- stokowski:gate {"state":"review",
"status":"rework","run":1} -->` from before the migration counts toward
the gate's `max_rework` after the migration. Cadence's first new
rework will emit `<!-- cadence:gate {"state":"review","status":"rework",
"rework_to":"implement"} -->`, picking up where Stokowski left off.

### 6. Stop Stokowski, start Cadence

Pick an invocation mode (see README for details):

- **Remote (`/schedule`)** — create a routine running `/cadence:tick`
  every minute, with Linear MCP and `GH_TOKEN` configured.
- **Local (`/loop`)** — `claude /loop 1m /cadence:tick` from the
  repo, after `gh auth login`.

Stop the Stokowski daemon (`stokowski stop` or however your install is
managed). The first Cadence fire will pick up the highest-priority
eligible issue and resume the workflow.

### 7. Verify, then delete Stokowski

Watch a few cycles through Linear. Once you're confident:

- Remove the Stokowski install from the repo.
- Remove Python deps if they were Stokowski-only.
- Remove any CI / cron wiring that started Stokowski.

The Cadence plugin doesn't live in the repo (it's installed at the
Claude Code level), so there's nothing extra to clean up there.

---

## Common gotchas

- **Two states with the same Linear column.** Cadence will refuse to
  start until they're distinct. The error names both states.
- **Subagent tries to post to Linear.** It shouldn't — the bootstrap
  posts the returned summary. Audit the subagent body for any
  Linear-touching instructions left over from Stokowski.
- **Implementer force-pushes during rework.** Don't allow it. The
  reviewer needs to see what changed since the last review. Push
  additional commits on top.
- **`pickup_state` collision.** Cadence's bootstrap will refuse a config
  where `pickup_state` equals any state's `linear_state`. Pick a column
  Cadence does not otherwise reference.
