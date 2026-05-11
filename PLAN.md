# Cadence — Plan

A reusable replacement for [Stokowski's](https://github.com/Sugar-Coffee/stokowski) supervisor, built on Claude Code subagents + `/schedule` (or `/loop`) + Linear MCP. Packaged as a Claude Code plugin — **Cadence** — so consuming projects install it once and configure their workflow declaratively.

This document is the single source of truth for the build. It is written so a fresh Claude session can implement any one of the three build sessions (A / B / C) without reading any other repository (including Stokowski).

---

## Goal

Replace the per-project Python daemon with a packaged Claude Code plugin that any repo can adopt by:

1. Installing the plugin (drops subagents + commands + workflow scaffolding into the repo).
2. Editing one `workflow.yaml` and the subagent prompts to taste.
3. Creating a single `/schedule` routine pointing at the plugin's bootstrap command.

Same end behaviour as Stokowski: Linear issues flow through a state machine, agents do the work, humans approve at gates, PRs land.

## Non-goals

- A long-running daemon. Each fire is one shot.
- Live TUI / web dashboard. Linear + GitHub are the UIs.
- True intra-fire parallelism. Concurrency comes from cron cadence + state-as-lock.
- Session resume across fires. Each fire reads workflow state from Linear directly; rework context (if any) is reconstructed from Linear comments.

---

## Architecture

```
   /schedule or /loop fires on cron interval
        |
        v
   bootstrap routine prompt (static, set at routine creation)
        |
        v
   slash command in the repo: /cadence-tick
        |
        +-- 1. Pick next eligible issue from Linear
        +-- 2. Acquire soft lock: add cadence-active label
              (and move from pickup_state to entry state if new)
        +-- 3. Read issue's current Linear state — this is the workflow state
        +-- 4. For gates: branch on waiting / approved / rework Linear column
        +-- 5. Invoke matching subagent via Agent tool
        |         |
        |         v
        |    subagent runs in fresh context, returns summary string
        |
        +-- 6. Post tracking comment, move Linear state to next, release lock
        +-- 7. Exit (next fire picks up the next issue, or continues this one)
```

The bootstrap routine prompt is short and fixed. Everything dynamic — workflow shape, prompts, model choices, tool restrictions — lives in the consuming repo's `.claude/` directory.

---

## Package structure

Plugin name: `cadence`. The plugin lives at the **repo root** (`c:\Code\Cadence\`) — the `cadence/` shown below is the conceptual layout, not a subdirectory.

```
<repo-root>/
  plugin.json                    # Claude Code plugin manifest (schema fetched from Anthropic docs at build time)
  commands/
    cadence-tick.md              # /cadence-tick — the bootstrap (heart of system)
    cadence-init.md              # /cadence-init — scaffolds workflow.yaml + subagent stubs into the consuming repo
    cadence-sweep.md             # /cadence-sweep — stale-lock cleanup
    cadence-status.md            # /cadence-status — human-facing status view
  agents/
    _template-planner.md         # starter subagents the consumer customises
    _template-implementer.md
    _template-reviewer.md
  templates/
    workflow.example.yaml        # copied by /cadence-init
    global-prompt.example.md     # copied by /cadence-init
  README.md                      # consumer-facing setup guide
  MIGRATION.md                   # Stokowski → Cadence guide
```

Consumer repo after `/cadence-init`:

```
<repo>/
  .claude/
    agents/
      planner.md                 # consumer-owned, edited freely
      implementer.md
      reviewer.md
    workflow.yaml                # state machine config
    prompts/
      global.md                  # context shared across all subagents
```

The plugin's templates seed sensible defaults; the consumer owns the resulting files.

---

## Plugin manifest

Use whatever schema the **current** Anthropic docs prescribe. The build session is expected to WebFetch the latest plugin authoring docs (e.g. claude.com/claude-code, docs.anthropic.com) before writing `plugin.json` rather than guessing. At minimum the manifest must declare:

- Plugin name (`cadence`)
- Version
- The four slash commands (`/cadence-tick`, `/cadence-init`, `/cadence-sweep`, `/cadence-status`)
- The three starter subagents
- Any required permissions / MCP servers the consumer must wire up

Document **both** install paths in README:

```
# Local checkout (development / iteration)
claude plugin install /path/to/cadence

# From GitHub
claude plugin install github:BenGGolden/cadence
```

(Exact CLI syntax: derive from current docs at build time.)

---

## Subagent definitions (starter set)

Each subagent is a `.md` file with frontmatter. The plugin ships `_template-*.md` files in `cadence/agents/`; `/cadence-init` copies them into the consumer's `.claude/agents/` under their final names.

**planner.md** — Opus, read-only tools. Planning is the highest-leverage step (bad plan poisons everything downstream), small token budget, ambiguity-heavy — pay for the strongest reasoning here.
```yaml
---
name: planner
description: Breaks down a Linear issue into a concrete implementation plan. Returns a plan summary string.
model: opus
tools: [Read, Grep, Glob, WebFetch, Bash]   # Bash for git log/diff, no Edit/Write
---
```

**implementer.md** — Sonnet, full tools + `gh` CLI via Bash. Implementation is high-volume, well-scoped per step, fast-iteration — Sonnet is the right cost/speed/quality tradeoff.
```yaml
---
name: implementer
description: Implements the plan from the prior planning comment. Opens or updates a PR. Returns a summary string with the PR URL.
model: sonnet
tools: [Read, Edit, Write, Bash, Grep, Glob]
---
```

**reviewer.md** — Sonnet, no Bash. Defensible either way; bump to Opus if catch-rate on subtle issues matters more than cost.
```yaml
---
name: reviewer
description: Reviews an open PR linked to the Linear issue. Returns review findings as a summary string.
model: sonnet
tools: [Read, Grep, Glob, WebFetch]
---
```

### Subagent contract (load-bearing)

**Subagents do NOT have Linear MCP access in their tools list.** The bootstrap is the sole writer to Linear.

| Responsibility | Owner |
|---|---|
| Read Linear issue + comments | Bootstrap (passes context into invocation) |
| Write `<!-- cadence:state -->` / `<!-- cadence:gate -->` / `<!-- cadence:reconcile -->` tracking comments | Bootstrap |
| Write work-product Linear comments (Workpad, plan summary, review findings, PR link) | Bootstrap (using subagent's returned summary string) |
| Move Linear state column | Bootstrap |
| Add / remove `cadence-active` label | Bootstrap |
| Read code, run tests, edit files | Subagent |
| Use `git`, `gh` CLI, run lints / type-checks | Subagent (Bash) |
| Open / update GitHub PR | Subagent (`gh pr create`, `gh pr view`, etc.) |

GitHub auth:
- **Local (`/loop`)** mode: consumer runs `gh auth login` ahead of time.
- **Remote (`/schedule`)** mode: configure `GH_TOKEN` on the routine.

Subagent return contract:
- Return a Markdown string. Bootstrap posts it verbatim as a Linear comment (after the state-tracking comment).
- For the implementer, the string must include the PR URL.
- If the subagent cannot complete its work (genuine blocker — missing auth, missing dependency), it errors. Bootstrap treats any exception as a failed attempt and records it.

---

## Lifecycle context block (bootstrap → subagent)

Whenever the bootstrap invokes a subagent via the Agent tool, it prepends a structured **Lifecycle Context** block to the subagent's user prompt. This is identical in spirit to Stokowski's three-layer prompt assembly (global prompt + stage prompt + auto-injected lifecycle), but assembled in prose by the bootstrap.

Exact block the bootstrap composes and includes at the top of the invocation prompt:

```markdown
<!-- AUTO-GENERATED BY CADENCE — DO NOT EDIT -->

## Lifecycle Context

- **Issue:** {identifier} — {title}
- **URL:** {url}
- **State:** {state_name}
- **Attempt:** {attempt_number}
- **Priority:** {priority}
- **Branch (Linear suggested):** {branchName}
- **Labels:** {labels}

### Description

{description, or "No description provided."}

### Transitions

- On success → **{state.next}** (Linear: "{next.linear_state}")
- (If gate downstream: brief note on what the human will see.)

### Rework Context

(Present ONLY if entering via the rework branch.)

This is a **rework run** at state `{state_name}`. A previous submission was reviewed and sent back. Address the feedback below before resubmitting.

> {human comment 1 body}
> — {createdAt}

> {human comment 2 body}
> — {createdAt}

### When Done

Do the work described by your subagent definition. When you are finished, return a Markdown summary of:
- What you changed (files, branch, PR URL if relevant).
- What you verified (tests passed, lints clean, etc.).
- Anything the next state will need.

Do NOT post anything to Linear yourself. Do NOT modify Linear state. The bootstrap will handle those.

<!-- END CADENCE LIFECYCLE -->
```

After this block, the bootstrap appends the contents of `.claude/prompts/global.md` (the consumer's shared preamble), and that's the full prompt the subagent receives. The subagent's `.md` body provides the role-specific instructions (its system prompt) and is loaded by Claude Code automatically when the Agent tool dispatches it.

---

## Workflow definition format

Each workflow state maps 1:1 to a distinct Linear state. Linear state IS the workflow state — no aliasing or disambiguation needed.

```yaml
# .claude/workflow.yaml
linear:
  team: ENG
  project_slug: abc123def456
  pickup_state: "Backlog"                 # New issues here are eligible for entry

label:
  cadence_active: "cadence-active"        # soft lock label
  cadence_needs_human: "cadence-needs-human"  # set when an issue hits max_attempts; excludes it from pickup

limits:
  max_attempts_per_issue: 3               # counted via tracking comments
  stale_after_minutes: 30                 # /cadence-sweep clears cadence_active locks older than this

entry: plan                               # First workflow state for new issues

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
    linear_state: "In Review"             # Waiting state (no human decision yet)
    approved_linear_state: "Approved"     # Human moves here to approve
    rework_linear_state: "Needs Rework"   # Human moves here to request rework
    on_approve: done
    on_rework: implement
    max_rework: 2

  done:
    type: terminal
    linear_state: "Done"
```

The bootstrap reads this file each fire. No restart needed for config changes.

### Validation rules (enforced at the top of `/cadence-tick`)

1. Every `linear_state` (and every gate's `approved_linear_state` / `rework_linear_state`) must be **unique** across the whole config. Two workflow states cannot share a Linear state. On collision: the bootstrap exits with a clear error message (no Linear writes).
2. Exactly one `entry` state. The named state must exist and be of `type: agent`.
3. Every `next` and `on_approve` / `on_rework` target must reference a defined state.
4. Each `state.subagent` must reference an existing file under `.claude/agents/{name}.md`.

If validation fails the bootstrap exits cleanly (no lock acquired, no Linear writes) and the next fire will fail the same way until the consumer fixes it.

---

## Tracking comment protocol

**Linear state is the source of truth for workflow state.** Tracking comments are audit trail, attempt accounting, and rework context — not the state source.

Comment shape (what the bootstrap emits):

```
<!-- cadence:state {"state": "plan", "attempt": 1, "started_at": "2026-05-10T14:23:01Z"} -->
**[Cadence]** Entering state: **plan** (attempt 1)
```

```
<!-- cadence:state {"state": "implement", "attempt": 1, "started_at": "2026-05-10T14:31:55Z", "from": "plan"} -->
**[Cadence]** Entering state: **implement** (attempt 1)
```

```
<!-- cadence:gate {"state": "review", "status": "waiting"} -->
**[Cadence]** Awaiting human review at **review**.
```

```
<!-- cadence:reconcile {"observed_linear_state": "Approved", "expected_state": "implement", "reason": "human reassigned"} -->
**[Cadence]** Detected human-driven state change; proceeding from Linear's state.
```

**Parser compatibility**: the bootstrap also recognises the legacy Stokowski shape `<!-- stokowski:state {"state","run","timestamp"} -->` and `<!-- stokowski:gate ... -->` and treats `run` as `attempt`, `timestamp` as `started_at`. This lets a repo migrating from Stokowski preserve attempt history without rewriting comments.

What comments are used for:
- **Attempt counting** — `limits.max_attempts_per_issue` is enforced by counting prior `cadence:state` entries for the current state.
- **Rework context** — when a gate rejects, the bootstrap gathers human comments posted *after* the last `cadence:state` entry to pass into the rework subagent.
- **Audit trail** — humans can read the history of state transitions and attempts.
- **Consistency check** — on each fire, the bootstrap compares the latest tracking comment's state to the current Linear state. If they disagree (e.g., a human dragged the issue between columns), the bootstrap trusts Linear, posts a `cadence:reconcile` comment noting the drift, and proceeds from Linear's state.

---

## Bootstrap prompt (the `/cadence-tick` body)

The full contents of `commands/cadence-tick.md` (the slash command body) is roughly the following. Session B writes this prose; it must be specific enough that a fresh agent running it produces correct behaviour every fire.

```
You are the dispatch tick for the Cadence workflow. Run exactly once and exit.

# Dry-run check
0. If invoked with an argument matching "dry-run" (case-insensitive), enter
   dry-run mode: read .claude/workflow.yaml, run all validation in step 3, and
   print the validation result + a summary of what a normal fire would do
   (which Linear states it would query, which subagent it would invoke given
   a hypothetical issue in the entry state, the exact Lifecycle Context block
   it would construct for that hypothetical). Do NOT call Linear MCP. Do NOT
   add labels, post comments, move state, or invoke any subagent. End the
   report with "DRY RUN — no side effects." and exit.

# Setup
1. Read .claude/workflow.yaml. If missing, print a clear error to the user
   and exit. Do not write to Linear.
2. Read .claude/prompts/global.md (or use empty string if missing).
3. Validate the workflow config:
   - All linear_state / approved_linear_state / rework_linear_state values
     unique across the whole config. (If a duplicate is found, error out
     naming both states.)
   - `entry` references a defined state of type: agent.
   - Every `next`, `on_approve`, `on_rework` target exists.
   - Every `state.subagent` resolves to .claude/agents/{name}.md.
   If any validation fails, print the error and exit (no Linear writes).
4. Build the set of "workflow Linear states" = pickup_state + every state's
   linear_state + every gate's approved_linear_state and rework_linear_state.

# Pick work
5. Using the Linear MCP, query the configured team/project for issues where:
   - Linear state ∈ workflow Linear states
   - cadence_active label NOT set (soft-lock check)
   - cadence_needs_human label NOT set (escalated issues are excluded until a
     human removes the label)
   - All blockers resolved (Linear's blocking relations — every blocker must
     be in a terminal Linear state, i.e. one not in the workflow set)
   Sort by priority (lower numeric = higher priority) then created_at
   ascending. Keep this as an ordered candidate list — step 6 may need to
   advance through it on lock-race losses. If the list is empty, print
   "No eligible issues." and exit.

# Acquire soft lock
6. Take the top candidate. Add the cadence_active label via Linear MCP.
   Re-read the issue to verify the label is now present. If the issue
   already had the label set by a concurrent fire (label race), skip it,
   advance to the next candidate in the list, and try again. Try up to
   3 candidates total — if all three race-lose, exit cleanly (next fire
   will retry).
7. If the locked issue's Linear state == pickup_state, move it to the entry
   state's linear_state.

# Determine workflow state (Linear is the truth)
8. Read the issue's current Linear state. Find the workflow state whose
   linear_state (or, for gates, approved_linear_state / rework_linear_state)
   matches. Call this the **matched workflow state**. If no match, this is
   a workflow drift; post a plain comment naming the unmapped Linear state,
   release the lock, and exit.
9. Fetch comments. Find the latest <!-- cadence:state ... --> or
   <!-- cadence:gate ... --> (also accepting the legacy "stokowski:" prefix
   and field names — see Tracking comment protocol). Drift check: compare
   the **workflow state name** recorded in that comment to the matched
   workflow state from step 8. If the names differ (e.g. last comment says
   "plan" but Linear column maps to "implement"), a human reassigned the
   issue; post a <!-- cadence:reconcile ... --> comment noting the drift,
   then proceed using the matched workflow state from step 8. (A gate
   sitting in its approved_linear_state or rework_linear_state is NOT
   drift — the matched workflow state name is the gate itself, which equals
   the last tracking comment's state name.)

# Gate handling
10. If the matched workflow state is a gate, check which Linear column the
    issue is in:
    - linear_state (waiting): release the lock and exit. Human hasn't decided.
    - approved_linear_state:
        a. Move issue to on_approve target's linear_state.
        b. If on_approve target is type: terminal, remove the cadence_active
           label and exit (no subagent invocation; the Linear state change is
           the audit record).
        c. Otherwise set the **target state** for the rest of the fire to
           on_approve and continue at step 11.
    - rework_linear_state:
        a. Count prior <!-- cadence:gate {..."status":"rework"...} -->
           entries for this gate on this issue (legacy
           stokowski:gate status=rework also counts). If
           gate.max_rework is defined and count >= max_rework, post a
           cadence:gate {"state":"<gate>","status":"escalated"} comment,
           add the cadence_needs_human label, release the lock, and exit.
        b. Gather human comments posted *after* the last cadence:state /
           cadence:gate (or legacy stokowski) tracking comment — these become
           the rework context, passed to the subagent in step 13.
        c. Post a cadence:gate {"state":"<gate>","status":"rework",
           "rework_to":"<on_rework target>"} comment.
        d. Move issue to on_rework target's linear_state.
        e. Set the **target state** for the rest of the fire to on_rework
           and continue at step 11.

    If the matched workflow state is an agent state (not a gate), the
    **target state** is simply the matched workflow state. Continue at step 11.

# Attempt cap
11. Count prior **attempt-marker** cadence:state comments for the target
    state on this issue. An attempt-marker is a cadence:state (or legacy
    stokowski:state) comment for that state name WITHOUT a `status` field
    — these are emitted by step 12 at the start of an attempt. Comments
    with `"status":"failed"` are failure records, not attempt markers, and
    are not counted here. If the count is >= limits.max_attempts_per_issue,
    post a plain comment ("Cadence: max attempts reached at state X. Needs
    human intervention."), add the cadence_needs_human label, release the
    cadence_active lock, and exit.

# Invoke subagent
12. Let `attempt = (count from step 11) + 1`. Post a new
    `<!-- cadence:state {"state":"<target>","attempt":<attempt>,"started_at":"<ISO8601>"} -->`
    comment (omit any `status` field — this is an attempt-marker).
13. Compose the Lifecycle Context block (see PLAN.md "Lifecycle context
    block") for the **target state** (state_name, attempt, and the target
    state's `next`). Append the global.md contents. This is the user prompt
    for the subagent.
14. Invoke the subagent named in `<target_state>.subagent` via the Agent
    tool, with that prompt. Capture the returned summary string.

# Wrap up
15. Post the subagent's returned summary as a Linear comment (no machine
    prefix — this is the work-product comment).
16. Subagent returned without error. Look up the target state's `next` in
    workflow.yaml (target state, not the gate we may have come from). Then:
    - If `next` is `type: agent`: move Linear state to next.linear_state.
    - If `next` is `type: gate`: post a
      `<!-- cadence:gate {"state":"<next>","status":"waiting"} -->` comment
      announcing the gate, then move Linear state to next.linear_state
      (the gate's "waiting" column).
    - If `next` is `type: terminal`: move Linear state to next.linear_state
      (e.g. "Done"). No further comment.
17. Remove the cadence_active label.
18. Exit.

# Constraints
- If anything errors before step 6 (lock acquisition), exit without Linear
  side effects.
- If anything errors after step 6, attempt to remove the cadence_active
  label before exiting. If even that fails, the stale-lock sweeper will
  catch it.
- If the subagent throws an exception, post a
  `<!-- cadence:state {"state":"<target>","attempt":<attempt>,"status":"failed","error":"..."} -->`
  comment (same attempt number as the attempt-marker from step 12, plus a
  `status:"failed"` field — this is a failure record, NOT an attempt
  marker). Remove the lock, exit. Do NOT advance Linear state. Next fire
  will retry until max_attempts_per_issue.
- Never invoke more than one subagent per fire.
```

The agent reads this exactly once per fire. All the state machine logic lives here in prose.

---

## Concurrency model

Uniform rule for both invocation modes: **one tick/fire processes exactly one issue.** Parallelism comes from running multiple ticks/fires in parallel, not from fan-out within a single tick.

- Cron interval: 1 minute (configurable).
- Soft lock = `cadence-active` label (added during lock acquisition, plus the move from `pickup_state` to the entry state's `linear_state` if the issue is new). Race condition: two fires within the same second could both grab the same top issue. Mitigation: the label-add is the lock check — if a fire sees the label already set on the issue it just queried, it skips and retries with the next issue (up to 3 retries, then exit).
- Effective throughput at 1-min cron: 60 issues/hour, well past any realistic team's demand.

**Getting more concurrency**:

- **`/schedule` mode**: increase cron frequency, or stand up N routines with staggered offsets. The platform spawns overlapping fires automatically — each grabs a different issue via the soft lock.
- **`/loop` mode**: run N loop sessions on N worktrees (separate terminals, possibly separate operators on separate machines). Each session is independent; soft lock keeps them from colliding.

**Why not intra-tick fan-out?** A `/loop` tick could in principle invoke multiple subagents in parallel via the Agent tool. It's left out of the default design because it adds real complexity (worktree allocation, partial failure within a tick, slow-leg blocking the whole tick) for a problem already solved more simply by running multiple sessions. Available as a v2 escape hatch if single-operator + high-concurrency turns out to be a real need.

---

## Failure handling

- **Subagent error**: bootstrap catches and posts a `<!-- cadence:state {"state":"X","attempt":N,"status":"failed","error":"..."} -->` failure record (the attempt-marker from step 12 already established N as a counted attempt; this failure record is for the audit trail and is NOT counted by step 11). Releases lock. Next fire will retry until `max_attempts_per_issue`.
- **Bootstrap timeout (`/schedule` platform max)**: the platform kills the fire. The lock label persists until a human or sweeper removes it. Mitigation: a separate `/schedule` routine running `/cadence-sweep` every 15 min that removes the label from issues with no recent activity (see below).
- **Linear API down**: bootstrap fails before lock acquisition (step 6). No side effects. Next fire retries.
- **Permanent issue failure**: after `max_attempts_per_issue`, the bootstrap posts a needs-human comment + `cadence-needs-human` label. The label keeps it out of the queue until a human intervenes.

### `/cadence-sweep` semantics

The body of `commands/cadence-sweep.md`:

```
You are the Cadence stale-lock sweeper. Run exactly once and exit.

1. Read .claude/workflow.yaml to learn the cadence_active label name and the
   pickup_state.
2. Query Linear for all issues currently labelled cadence_active.
3. For each, compute time-since-last-update (use Linear's updatedAt). If
   the gap is greater than the configured stale_after_minutes (default 30),
   remove the cadence_active label and post a brief comment:
   "Cadence: stale lock cleared (last activity {timestamp})."
4. Exit.
```

Configurable via an optional `limits.stale_after_minutes` field in workflow.yaml (default 30).

---

## `/cadence-status` semantics

Read-only status view. Prints a Markdown table to the user's terminal. Body of `commands/cadence-status.md`:

```
You are the Cadence status reporter. Run exactly once and exit.

1. Read .claude/workflow.yaml.
2. Build the set of workflow Linear states (same as /cadence-tick step 4).
3. Query Linear for all issues in those states. For each, capture:
   - Identifier, title, current Linear state
   - cadence_active and cadence-needs-human label presence
   - Latest <!-- cadence:state ... --> attempt number, if any
4. Print a Markdown table with columns:
   Identifier | Title | State | Attempt | Locked? | Needs Human?
5. Below the table, print summary counts per workflow state.
6. Exit.
```

No Linear writes. Safe to run at any time.

---

## Consumer setup

Two invocation modes. Both use the same plugin and the same `/cadence-tick` command — they differ only in where the cron lives.

### Mode A — Remote (`/schedule`)

Fully autonomous, no operator presence required.

```
# In the consuming repo:
# 1. Install plugin
claude plugin install github:BenGGolden/cadence
# (or: claude plugin install /path/to/local/cadence)

# 2. Scaffold workflow
claude /cadence-init

# 3. Edit .claude/workflow.yaml, .claude/agents/*.md, .claude/prompts/global.md

# 4. Create a routine via /schedule:
#    - Schedule: */1 * * * *
#    - Repo: this repo
#    - Prompt: /cadence-tick
#    - Linear MCP configured on the routine
#    - GH_TOKEN env var configured on the routine

# 5. Watch Linear.
```

Second routine for the stale-lock sweeper:
- Schedule: `*/15 * * * *`
- Prompt: `/cadence-sweep`

### Mode B — Local (`/loop`)

Operator-tended, runs in a local Claude Code session.

```
# Steps 1-3 identical.

# 4. From an interactive Claude Code session in the repo, after gh auth login:
claude /loop 1m /cadence-tick

# 5. Leave the session running. Interrupt with Ctrl+C to pause.
```

No stale-lock sweeper needed — there's no remote platform timeout, and the operator can resolve stuck locks manually via `/cadence-status` (or by deleting the `cadence-active` label in Linear).

### Choosing a mode

| | Remote (`/schedule`) | Local (`/loop`) |
|---|---|---|
| Operator presence | Not required | Required |
| Per-fire setup cost | Fresh clone + install | Already-cloned repo, warm cache |
| Subagent timeout ceiling | Platform max (~30 min) | Effectively none |
| Multi-operator safety | Soft lock essential | Soft lock optional (single-op) / essential (multi-op) |
| Credentials | Configured on routine | Local MCP + `gh` CLI |
| Debug loop | Slow (remote logs) | Fast (live terminal) |
| Bus factor | Higher | 1 |

Teams that want CI-like "fire and forget" pick remote. Teams that want to watch their fleet and keep work on a laptop pick local. The plugin doesn't care which.

---

## Migration path from Stokowski

For repos currently using Stokowski:

1. **Linear board restructure**: Stokowski lets multiple internal states share one Linear state (e.g. `plan` and `implement` both map to "In Progress"). Cadence forbids this — each workflow stage needs its own Linear column. Audit your existing `workflow.yaml`, add the missing Linear columns (e.g. "Planning", "Implementing"), and update `linear_state` values accordingly.
2. **Workflow.yaml schema changes**: rename the top-level `tracker:` block to `linear:` and the `transitions:` map to a flat `next` (for agent states) / `on_approve` + `on_rework` (for gate states). Drop Stokowski-specific blocks the plugin does not use: `polling`, `workspace`, `hooks`, `claude`, `agent`, `server`, `linear_states` (Cadence's per-state `linear_state` field replaces the role mapping).
3. Move existing prompt `.md` files to `.claude/agents/*.md`, add subagent frontmatter (name, description, model, tools). Remove any "post your own Workpad" instructions — Cadence's bootstrap is the sole Linear writer.
4. Move the existing global prompt to `.claude/prompts/global.md`.
5. Existing `<!-- stokowski:state ... -->` comments are parsed transparently for attempt history (the bootstrap also reads `run` and `timestamp` field names). New comments will use the `cadence:` prefix and `attempt` / `started_at` field names — no rewrite required.
6. Stop the Stokowski daemon. Pick an invocation mode (Mode A `/schedule` or Mode B `/loop`) and set it up per the Consumer setup section.
7. Delete the Stokowski install once stable.

---

## Open questions (carry into operation; do not block build)

1. **`/schedule` platform behaviour with parallel fires**: do two routines firing within the same second see each other's label changes immediately? Worst case, add a 5-second random jitter at fire start.
2. **Subagent invocation cost in a scheduled remote fire**: subagents run in fresh context, so each fire pays full prompt-priming. With prompt caching this should be acceptable. Measure before optimising.
3. **Workspace setup hooks**: Stokowski's `after_create` hook (e.g. `npm install`) doesn't have an obvious analogue in `/schedule` mode — for repos with expensive setup, the remote agent platform's container image is the right place to bake deps. Document this; don't try to solve it inside the plugin.
4. **Gate escalation**: should the plugin support time-based escalation (e.g. "if a gate has been waiting > 48h, post a reminder")? v2.
5. **Multiple workflows per repo**: the design assumes one `workflow.yaml`. If a repo wants different workflows for different issue types, the workflow could grow a "selector" (label-based) or you run multiple routines with different config paths. Defer.

---

## Build sessions

The build is partitioned into three self-contained sessions. Each session's "Deliverables" list is the acceptance contract — when those exist and the verification step passes, the session is done.

Every session starts by re-reading this `PLAN.md` and the current Anthropic Claude Code plugin docs (WebFetch).

### Session A — Scaffolding (plugin skeleton + /cadence-init + starter subagents)

**Goal**: produce an installable Cadence plugin that scaffolds the consumer's `.claude/` directory correctly, but does not yet implement the dispatch tick.

**Deliverables** (all under `c:\Code\Cadence\`):
1. `plugin.json` — manifest conforming to current Anthropic plugin spec (fetch docs first). Declares the four slash commands and three starter subagents.
2. `commands/cadence-tick.md` — **stub only**: prints "TODO: implemented in session B" and exits. Frontmatter + description correct, body to be filled by session B.
3. `commands/cadence-init.md` — full implementation. Logic:
   - Refuse to overwrite an existing `.claude/workflow.yaml` unless invoked with a `--force` argument; print the existing path and exit.
   - Otherwise create `.claude/agents/`, `.claude/prompts/`, copy template files, write `.claude/workflow.yaml` from `templates/workflow.example.yaml`, write `.claude/prompts/global.md` from `templates/global-prompt.example.md`.
   - Print next-steps instructions (edit these files, then set up `/schedule` or `/loop`).
4. `commands/cadence-sweep.md`, `commands/cadence-status.md` — stubs only ("TODO: implemented in session C").
5. `agents/_template-planner.md`, `_template-implementer.md`, `_template-reviewer.md` — full frontmatter + body. Body content adapts the Stokowski-style prompts (investigate / implement / review) to the Cadence contract: subagents do NOT call Linear directly, they return a summary string. The implementer body covers branch creation, commits, `gh pr create`, rework path (push to existing branch, no force-push).
6. `templates/workflow.example.yaml` — annotated single-project workflow with `plan → implement → review (gate) → done` matching the example in PLAN.md.
7. `templates/global-prompt.example.md` — adapted from Stokowski's global.example.md. Drop the "post your own Workpad" instruction since the bootstrap now posts everything; keep the "no questions, no interactive commands, headless" rules.
8. `README.md` — install + setup walkthrough for both modes.
9. `MIGRATION.md` — Stokowski → Cadence walkthrough.

**Verification**:
- `plugin.json` parses cleanly (JSON validity).
- All command/agent files have valid frontmatter.
- Install the plugin into a throwaway Claude Code session. Run `/cadence-init` against an empty temp directory. Verify the expected file tree is produced.
- Run `/cadence-init` again — should refuse to overwrite.
- Run `/cadence-init --force` — should overwrite.
- Run `/cadence-tick` — should print the stub message and exit cleanly.

**Out of scope** for session A: the bootstrap prose itself, the sweeper logic, the status reporter logic.

### Session B — Bootstrap (/cadence-tick prose)

**Goal**: replace the `/cadence-tick` stub with the full dispatch prose described in this PLAN.

**Deliverables**:
1. `commands/cadence-tick.md` rewritten as full bootstrap (using the prose in this PLAN's "Bootstrap prompt" section as the template). Must include:
   - The dry-run branch (step 0).
   - All validation rules (step 3).
   - The Lifecycle Context block construction (step 13) — verbatim shape per PLAN.
   - The legacy `stokowski:` comment parser (step 9).
   - The race-loss retry loop (step 6).
   - The 3-retry exit (step 6).
2. Update `templates/workflow.example.yaml` if any field is missing for the bootstrap to work (e.g. add `limits.stale_after_minutes` as a commented-out optional field, since session C will need it).

**Verification**:
- Run `/cadence-tick dry-run` in a repo where `/cadence-init` has been executed. Dry-run does NOT call Linear MCP — it only reads config, validates, and prints the Lifecycle Context block it *would* compose for a hypothetical entry-state issue. Output must include "DRY RUN — no side effects."
- Read the file and walk through each of the 18 numbered steps; confirm each maps to a clear instruction the runtime agent can follow without ambiguity.
- Mock-run by hand against synthetic issue descriptions covering: entry-state issue with no comments, gate→approve, gate→rework with max_rework exceeded, attempt cap exceeded. Walk the prose for each; the routing should match this PLAN.

**Out of scope**: sweeper, status, docs.

### Session C — Operations (/cadence-sweep + /cadence-status + polish)

**Goal**: ship the operational commands and finalise docs.

**Deliverables**:
1. `commands/cadence-sweep.md` — full prose per PLAN. Honours `limits.stale_after_minutes` from workflow.yaml.
2. `commands/cadence-status.md` — full prose per PLAN. Read-only; produces Markdown table + summary.
3. README updates:
   - Sample Markdown output of `/cadence-status` so consumers know what to expect.
   - Troubleshooting section: stale locks, max-attempts hit, validation errors, the `cadence-needs-human` label workflow.
4. MIGRATION.md polish: explicit example showing a `<!-- stokowski:state ... -->` comment surviving migration.
5. End-to-end smoke checklist (manual): a single page listing the steps to validate against a real throwaway Linear project after merge.

**Verification**:
- Run `/cadence-sweep` in dry-run terms (read the prose, walk through it against synthetic data).
- Run `/cadence-status` against a freshly-`/cadence-init`-ed repo with no Linear data; it should produce an empty table without errors.
- Spell-check / link-check the README and MIGRATION.

---

## What is intentionally NOT in the build

- No automated tests against a live Linear API. The end-to-end smoke checklist (session C deliverable 5) is the manual verification.
- No CI for the plugin itself in v1. If we publish to a marketplace later, CI joins then.
- No telemetry. The audit trail is the Linear comment history.
