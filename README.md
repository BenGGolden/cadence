# Cadence

A Claude Code plugin that turns Linear into a multi-agent workflow runner.
Issues flow through a state machine you define; subagents do the work;
humans approve at gates; PRs land. No long-running daemon — each tick is
one shot, fired by `/schedule` or `/loop`.

Cadence is a reusable, packaged replacement for the per-project
[Stokowski](https://github.com/Sugar-Coffee/stokowski) supervisor. Consuming
projects install the plugin, run `/cadence:init`, edit one YAML
file and three subagent prompts, point a scheduled routine at
`/cadence:tick`, and watch Linear.

> **Build status:** v1 scaffolding is complete. All four slash commands
> (`/cadence:init`, `/cadence:tick`,
> `/cadence:sweep`, `/cadence:status`) are implemented
> against the design in [PLAN.md](./PLAN.md). End-to-end smoke testing
> against a live Linear project happens once per consuming repo
> ([SMOKE.md](./SMOKE.md) is the checklist).

---

## What it does

```
   /schedule or /loop fires on cron interval
        |
        v
   /cadence:tick (Cadence bootstrap)
        |
        +-- 1. Pick next eligible Linear issue
        +-- 2. Acquire soft lock (cadence-active label)
        +-- 3. Read current Linear state → workflow state
        +-- 4. For gates: branch on waiting / approved / rework
        +-- 5. Invoke matching subagent in fresh context
        +-- 6. Post tracking comment, move Linear state, release lock
        +-- 7. Exit (next fire picks up the next issue, or continues this one)
```

Workflow state lives in Linear columns. There is no separate database,
no orchestrator process, no resume-from-checkpoint. State machine
behaviour is described in prose in `commands/tick.md` — that
prose IS the dispatch logic.

---

## Install

### Local checkout (development)

```bash
claude --plugin-dir /path/to/cadence
```

This loads the plugin into a session without permanent install. Useful
while you're iterating on the plugin itself.

### Persistent install via marketplace

Cadence has not yet been published to a marketplace. To install the
current source into a project for ongoing use, either:

1. **Clone and load via flag** — clone this repo, then add
   `claude --plugin-dir /path/to/cadence` to whatever launches Claude
   Code in your consumer repo (a wrapper script, a shell alias, a
   `.envrc`).

2. **Roll your own marketplace** — set up a personal marketplace
   pointing at this repo, then `claude plugin install cadence@<your-mp>`.
   See the [plugin marketplaces docs][marketplaces].

Once the plugin lists, slash commands appear under the `cadence:`
namespace:

| File                          | Invocation                       |
|-------------------------------|----------------------------------|
| `commands/tick.md`            | `/cadence:tick`                  |
| `commands/init.md`            | `/cadence:init`                  |
| `commands/sweep.md`           | `/cadence:sweep`                 |
| `commands/status.md`          | `/cadence:status`                |

[marketplaces]: https://code.claude.com/docs/en/plugin-marketplaces

---

## Consumer setup

Both invocation modes use the same plugin and the same
`/cadence:tick` command. They differ in where the cron lives.

### Mode A — Remote (`/schedule`)

Fully autonomous. No operator presence required between fires.

```bash
# 1. Install plugin (see above).

# 2. From the consuming repo's Claude Code session:
/cadence:init

# 3. Edit the scaffolded files:
#    .claude/workflow.yaml        ← Linear team, project, state names
#    .claude/agents/planner.md    ← model, tools, system prompt
#    .claude/agents/implementer.md
#    .claude/agents/reviewer.md
#    .claude/prompts/global.md    ← shared preamble

# 4. Create a /schedule routine:
#       Schedule:  */1 * * * *
#       Repo:      this repo
#       Prompt:    /cadence:tick
#       MCP:       Linear MCP server
#       Env:       GH_TOKEN (for gh CLI in the implementer)

# 5. Create a second /schedule routine for stale-lock cleanup:
#       Schedule:  */15 * * * *
#       Prompt:    /cadence:sweep

# 6. Watch Linear.
```

### Mode B — Local (`/loop`)

Operator-tended. Runs in a local Claude Code session in the repo.

```bash
# Steps 1-3 identical to Mode A.

# 4. From an interactive Claude Code session in the repo, after gh auth login:
claude /loop 1m /cadence:tick

# 5. Leave the session running. Interrupt with Ctrl+C to pause.
```

No stale-lock sweeper needed — `/loop` has no platform timeout that could
strand a lock, and you can clear `cadence-active` manually in Linear if
needed.

### Choosing a mode

|                          | Remote (`/schedule`)        | Local (`/loop`)                |
|--------------------------|-----------------------------|--------------------------------|
| Operator presence        | Not required                | Required                       |
| Subagent timeout ceiling | Platform max (~30 min)      | Effectively none               |
| Multi-operator safety    | Soft lock essential         | Soft lock essential if multi-op |
| Credentials              | Configured on routine       | Local MCP + `gh auth login`    |
| Debug loop               | Slow (remote logs)          | Fast (live terminal)           |
| Bus factor               | Higher                      | 1                              |

Teams that want CI-like "fire and forget" pick remote. Teams that want to
watch their fleet and keep work on a laptop pick local.

---

## Watching the workflow

Once the routines are running, the human-facing view is `/cadence:status`.
It's read-only and safe to run any time:

```
$ claude /cadence:status
```

Sample output for a small workflow with three live issues:

```markdown
## Cadence status — 2026-05-11T14:23:01Z

Team: **ENG**   Project: **acme-platform**   Pickup: **Backlog**

### Issues in workflow

| ID      | Title                                              | Linear column | Workflow state    | Attempt | Lock | Needs human |
|---------|----------------------------------------------------|---------------|-------------------|---------|------|-------------|
| ENG-204 | Add OAuth callback retry on transient 5xx          | Implementing  | implement         | 2       | 🔒   |             |
| ENG-198 | Migrate analytics worker to BullMQ                 | In Review     | review (waiting)  | 1       |      |             |
| ENG-187 | Crash on empty rate-limit header                   | Needs Rework  | review (rework)   | 2       |      |             |
| ENG-176 | Tighten auth middleware regex                      | Backlog       | (pickup)          | —       |      |             |
| ENG-149 | Reindex legacy events                              | Implementing  | implement         | 3       |      | 🛑          |

### Per-state counts

- **(pickup)** (`Backlog`) — 1 issues
- **plan** (`Planning`) — 0 issues
- **implement** (`Implementing`) — 2 issues   🔒 1 locked   🛑 1 needs-human
- **review** (gate)
  - waiting (`In Review`) — 1 issues
  - approved (`Approved`) — 0 issues
  - rework (`Needs Rework`) — 1 issues
- **done** (`Done`) — 0 issues

Read-only — no Linear writes performed.
```

The lock (🔒) and needs-human (🛑) columns are the operational signals.
A lock means a tick is in flight (or a stale lock the sweeper hasn't
caught yet). Needs-human means the issue hit the attempt cap or rework
cap and is now sidelined until a human removes the
`cadence-needs-human` label.

---

## Troubleshooting

### Stale locks (Mode A, `/schedule`)

**Symptom**: an issue is stuck with the `cadence-active` label set, but
no fire seems to be doing anything with it. The status report shows 🔒
but the issue's `updatedAt` is older than your tick interval.

**Cause**: a previous `/cadence:tick` fire was killed mid-tick
(platform timeout, network drop) before it could remove its own label.

**Fix**: the `/cadence:sweep` routine clears these automatically
on its cadence (default every 15 minutes — see Mode A setup). For an
immediate clear, run `/cadence:sweep` once interactively, or
manually delete the `cadence-active` label in Linear. The next
`/cadence:tick` fire will pick the issue back up.

The sweeper's threshold is configurable in `.claude/workflow.yaml`:

```yaml
limits:
  stale_after_minutes: 30   # default
```

### Max attempts reached

**Symptom**: an issue is sidelined with the `cadence-needs-human` label,
and its Linear comments include a `[Cadence] Max attempts (N) reached at
state X` line.

**Cause**: the same workflow state failed `limits.max_attempts_per_issue`
times in a row (default 3). The bootstrap has stopped retrying it
automatically.

**Fix**:
1. Read the failure records (`<!-- cadence:state {"status":"failed",...} -->`)
   to understand what went wrong. Common causes: missing credentials,
   genuinely impossible task, flaky test that fails on the implementer's
   environment, ambiguous Linear description.
2. Address the root cause — fix the env, clarify the issue description,
   or break the task down.
3. Remove the `cadence-needs-human` label in Linear. The next
   `/cadence:tick` fire will pick the issue up. The attempt
   counter is **not** reset by removing the label — if you want a clean
   slate, also delete the prior attempt-marker comments (the bootstrap
   counts them on every fire).
4. If the issue needs to be permanently abandoned, move it to a Linear
   column outside the workflow (e.g. "Cancelled") and leave the
   needs-human label set.

### Rework limit reached

**Symptom**: similar to max attempts, but the failure comment names a
gate: `[Cadence] Rework limit reached at gate <name>`.

**Cause**: the gate's `max_rework` was exceeded — the human kept moving
the issue to the rework column and the subagent kept not satisfying
review.

**Fix**: same shape as max-attempts. Read the rework comments, fix the
underlying problem (often a clearer review comment or a smaller scope),
remove `cadence-needs-human`. The rework counter isn't reset by label
removal — delete prior `<!-- cadence:gate {"status":"rework"} -->`
comments if you want it to.

### Validation errors

**Symptom**: every `/cadence:tick` fire exits immediately with a
config error, no Linear writes happen.

**Cause**: `.claude/workflow.yaml` violates a validation rule (duplicate
Linear column, undefined target, missing subagent file).

**Fix**: read the error — it names the offending keys. Fix
`.claude/workflow.yaml`. The next fire will succeed; no restart needed.
Run `/cadence:tick dry-run` to confirm before going live again.

### Issue moved to an unmapped state

**Symptom**: a Linear comment reads `Issue moved to unmapped Linear
state <X> between pickup and dispatch; releasing lock without action`,
and the issue keeps being picked up and immediately released.

**Cause**: a human (or another tool) moved the issue to a Linear column
that's not part of `workflowLinearStates`.

**Fix**: either move the issue back to a workflow column, or add the
column to `workflow.yaml`, or remove the issue's `cadence-active` label
and accept that it's now out of band.

### Drift reconciliation

**Symptom**: a Linear comment reads `Detected human-driven state change;
proceeding from Linear's state.` (a `<!-- cadence:reconcile ... -->`
tracking comment).

**Cause**: a human dragged the issue between Linear columns out of band.
The bootstrap detected the drift and is proceeding from Linear's
authoritative state.

**Fix**: usually none required — this is expected. If you didn't mean
to move it, just move it back and the next fire will re-reconcile.

### `/cadence:status` is slow

**Symptom**: status takes tens of seconds to render.

**Cause**: each issue's comment list is fetched separately to find its
latest attempt marker. Large workflows (hundreds of issues in flight)
amplify this.

**Fix**: the status reporter degrades gracefully — it renders attempt
counts as `?` when comment fetches fail. If you need to keep the table
populated, narrow the project scope (split into multiple Linear
projects with separate `workflow.yaml` files in separate repos), or
restrict the report by running it against a Linear filter view.

---

## Architecture in one glance

- **Linear state is the workflow state.** No aliasing. Each workflow
  stage maps 1:1 to a distinct Linear column. The bootstrap reads Linear
  on every fire to learn the current state.
- **Soft lock = a label.** `cadence-active` is added at lock acquisition,
  removed at the end of the fire. Stale ones are swept on a separate
  cadence.
- **One issue per fire.** Concurrency comes from cron frequency or from
  multiple parallel sessions, not from intra-fire fan-out.
- **Subagents are stateless and Linear-blind.** The bootstrap is the
  sole Linear writer. Subagents read code, make changes, run tests, open
  PRs, and return a Markdown summary string. The bootstrap posts that
  string verbatim as a Linear comment.
- **Plugin owns logic, consumer owns config.** The plugin ships the
  bootstrap prose, sweep / status logic, and subagent templates. The
  consumer owns `.claude/workflow.yaml`, `.claude/agents/*.md`, and
  `.claude/prompts/global.md`.

For the full design, read [PLAN.md](./PLAN.md). It is the single source of
truth for the build.

---

## Files this plugin scaffolds

`/cadence:init` creates the following in the consumer's repo:

```
<consumer-repo>/
└── .claude/
    ├── workflow.yaml             # state machine config
    ├── prompts/
    │   └── global.md             # shared subagent preamble
    └── agents/
        ├── planner.md            # Opus, read-only
        ├── implementer.md        # Sonnet, full edit + git/gh
        └── reviewer.md           # Sonnet, read-only
```

Re-running `/cadence:init` refuses to overwrite. Use
`/cadence:init --force` to replace existing files.

---

## Migration from Stokowski

See [MIGRATION.md](./MIGRATION.md). The short version:

1. Audit your Linear board — Cadence requires one column per workflow
   stage; Stokowski allowed many-to-one. Add the missing columns.
2. Rename `tracker:` → `linear:` in workflow.yaml; flatten the
   transitions block.
3. Move prompt files to `.claude/agents/*.md` with subagent frontmatter.
4. Move the global prompt to `.claude/prompts/global.md`.
5. Existing `<!-- stokowski:state ... -->` comments are parsed
   transparently — no rewrite needed for attempt history.
6. Pick `/schedule` or `/loop` and stop the daemon.

---

## Requirements

- Claude Code with plugin support (see [plugin docs][plugins-doc] for the
  minimum version).
- A Linear MCP server configured for the consuming repo (`/schedule`
  mode: on the routine; `/loop` mode: on your local Claude Code config).
- GitHub auth available to the implementer subagent: `gh auth login`
  locally, or `GH_TOKEN` env var on the routine.
- A Linear board with one column per workflow stage (Planning,
  Implementing, In Review, Approved, Needs Rework, Done by default —
  rename and reshape via `workflow.yaml`).

[plugins-doc]: https://code.claude.com/docs/en/plugins

---

## Non-goals

- A long-running daemon. Each fire is one shot.
- Live TUI / web dashboard. Linear + GitHub are the UIs.
- Intra-fire parallelism. Concurrency comes from cron cadence + soft
  lock.
- Session resume across fires. Each fire reconstructs context from
  Linear directly.

---

## License

MIT. See [LICENSE](./LICENSE) if present.
