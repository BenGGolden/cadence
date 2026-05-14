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
> `/cadence:sweep`, `/cadence:status`) are implemented. End-to-end smoke
> testing against a live Linear project happens once per consuming repo
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

**Local checkout (development)** — `claude --plugin-dir /path/to/cadence` loads
the plugin into a session without a permanent install; useful while iterating
on the plugin itself.

**Persistent install** — Cadence is not yet published to a marketplace. Either
clone this repo and add the `--plugin-dir` flag to whatever launches Claude Code
in your consumer repo (wrapper script, shell alias, `.envrc`), or roll your own
marketplace pointing at this repo and `claude plugin install cadence@<your-mp>`
(see the [plugin marketplaces docs][marketplaces]).

Once loaded, the four slash commands appear under the `cadence:` namespace:
`/cadence:tick`, `/cadence:init`, `/cadence:sweep`, `/cadence:status`.

[marketplaces]: https://code.claude.com/docs/en/plugin-marketplaces

---

## Consumer setup

Both invocation modes use the same plugin and the same `/cadence:tick`
command — they differ only in where the cron lives. You need: Claude Code with
plugin support, a Linear MCP server, GitHub auth for the implementer subagent
(`gh auth login` locally or `GH_TOKEN` on the routine), and a Linear board with
one column per workflow stage (defaults: Todo, Planning, Implementing, In
Review, Approved, Needs Rework, Done — reshape via `workflow.yaml`).

### Mode A — Remote (`/schedule`)

Fully autonomous. No operator presence required between fires.

1. Install the plugin (see above).
2. Run `/cadence:init` in the consuming repo's Claude Code session.
3. Edit the scaffolded files under `.claude/` — `workflow.yaml` (Linear team,
   project, state names), `agents/*.md` (model, tools, system prompt),
   `prompts/global.md` (shared preamble).
4. **Make Linear's MCP server reachable from the routine.** Cloud routines do
   NOT inherit MCP servers added locally via `claude mcp add`. Either set up an
   account-level connector ([claude.ai/customize/connectors][connectors] →
   Linear → OAuth — recommended, since Linear's OAuth flow is web-only), or
   commit a `.mcp.json` at the consumer repo root. The same applies to any
   other MCP server the subagents need.
5. **Create a `/schedule` routine** ([claude.ai/code/routines][routines]):
   schedule `*/1 * * * *`, prompt `/cadence:tick`, Linear connector on,
   `GH_TOKEN` set on the routine's cloud environment, and Linear MCP tools +
   `Agent` + `Bash` + `Read`/`Write`/`Edit` set to Always Allow — an unattended
   routine hangs on any permission prompt. Bake expensive repo setup (npm
   install, native deps) into the cloud environment's setup script rather than
   rerunning it every fire.
6. **Create a second routine for stale-lock cleanup:** schedule `*/15 * * * *`,
   prompt `/cadence:sweep`, same connectors / env / permissions as the tick
   routine.
7. Watch Linear.

[connectors]: https://claude.ai/customize/connectors
[routines]: https://claude.ai/code/routines

### Mode B — Local (`/loop`)

Operator-tended. Steps 1–3 are identical to Mode A. Then, from an interactive
Claude Code session in the repo (after `gh auth login`), run
`claude /loop 1m /cadence:tick` and leave it running — Ctrl+C to pause.

No stale-lock sweeper needed — `/loop` has no platform timeout that could
strand a lock, and you can clear `cadence-active` manually in Linear if needed.

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

## Required permissions

Cadence is a system of slash commands and subagents — each call hits the
Claude Code permission system. In Mode A (`/schedule`) every tool the
bootstrap or a subagent calls **must be pre-allowed** on the routine,
because a remote routine has no human to answer permission prompts and
will hang or fail on the first prompt. In Mode B (`/loop`) you can
approve interactively, but pre-allowing the same set lets the loop run
unattended for stretches without stalling on prompts.

The lists below are the minimum surface for the shipped templates. If
you edit `.claude/agents/*.md` `tools:` lines or extend the bootstrap
prose, adjust accordingly.

### Bootstrap (`/cadence:tick`, `/cadence:sweep`, `/cadence:status`)

| Tool        | Why                                                          |
|-------------|--------------------------------------------------------------|
| `Read`      | Read `.claude/workflow.yaml`, `.claude/prompts/global.md`, subagent files. |
| `Bash`      | Generate the current UTC timestamp for tracking-comment JSON (`date -u …` or `Get-Date …`), and run the three Python helper scripts under the plugin's `scripts/` directory (config validation, comment parsing, tracking-comment emission). |
| `Agent`     | Invoke planner / implementer / reviewer subagents.           |
| `TodoWrite` | Optional — only if you want progress visibility on long fires. |

### Subagents (shipped template defaults — edit per repo)

| Subagent       | Tools declared in frontmatter                         |
|----------------|-------------------------------------------------------|
| `planner`      | `Read, Grep, Glob, WebFetch, Bash`                    |
| `implementer`  | `Read, Edit, Write, Bash, Grep, Glob` (`Bash` covers `git` and `gh`) |
| `reviewer`     | `Read, Grep, Glob, WebFetch`                          |

### Linear MCP tools

Cadence makes a small, fixed set of Linear calls — three read-only (list
issues, read an issue, list comments) and two write (post a comment, update an
issue). Tool names vary by MCP vendor; match by intent.

**Read tools — pre-allow only what Cadence calls.**
The prose reaches for exactly three read-only tools. Set these to
Always Allow:

| Intent          | Common names                                                  |
|-----------------|---------------------------------------------------------------|
| List issues     | `list_issues`, `mcp__linear__list_issues`, `search_issues`    |
| Read an issue   | `get_issue`, `mcp__linear__get_issue`                         |
| List comments   | `list_comments`, `mcp__linear__list_comments`                 |

Leave the rest of the read-only category on Ask. "Read-only" is not
"harmless" — `get_document`, `list_users`, `list_projects`, etc. read
confidential data that has no business in a subagent's context, and a
hallucinated call could echo it into a comment the bootstrap posts verbatim.
Bulk-allowing the whole category is fine in a throwaway test workspace; on any
workspace with real data, pre-allow narrowly.

**Write tools — pre-allow only what Cadence calls.**
Set these to Always Allow:

| Intent                       | Common names                                                                                          |
|------------------------------|-------------------------------------------------------------------------------------------------------|
| Post a comment               | `save_comment`, `mcp__linear__create_comment`                                                         |
| Update issue (state, labels) | `save_issue`, `mcp__linear__update_issue`, `mcp__linear__add_label`, `mcp__linear__remove_label`      |

Leave every other write/delete tool on Ask (or off) — `create_attachment`,
`delete_comment`, `create_issue_label` (labels are created up front per SMOKE
prereqs, not by the plugin), `save_document`, `save_project`, and any other
workspace-wide mutation. Keeping these on Ask means a hallucinated call or
future prose change fails closed instead of silently mutating Linear.

### Environment variables

| Var        | Where to set (Mode A)                                | Why                                            |
|------------|------------------------------------------------------|------------------------------------------------|
| `GH_TOKEN` | Routine's cloud environment → Environment variables  | Implementer subagent's `gh` CLI calls.         |

In Mode B (`/loop`) `gh auth login` once locally; no env var needed.

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

Team: **ENG**   Project: **acme-platform**   Pickup: **Todo**

### Issues in workflow

| ID      | Title                                              | Linear column | Workflow state    | Attempt | Lock | Needs human |
|---------|----------------------------------------------------|---------------|-------------------|---------|------|-------------|
| ENG-204 | Add OAuth callback retry on transient 5xx          | Implementing  | implement         | 2       | 🔒   |             |
| ENG-198 | Migrate analytics worker to BullMQ                 | In Review     | review (waiting)  | 1       |      |             |
| ENG-187 | Crash on empty rate-limit header                   | Needs Rework  | review (rework)   | 2       |      |             |
| ENG-176 | Tighten auth middleware regex                      | Todo          | (pickup)          | —       |      |             |
| ENG-149 | Reindex legacy events                              | Implementing  | implement         | 3       |      | 🛑          |

### Per-state counts

- **(pickup)** (`Todo`) — 1 issues
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

### Stale locks (Mode A)

**Symptom:** an issue keeps the `cadence-active` label but no fire is acting on
it; `/cadence:status` shows 🔒 with an `updatedAt` older than the tick interval.
**Cause:** a `/cadence:tick` fire was killed mid-tick (platform timeout, network
drop) before removing its own label.
**Fix:** the `/cadence:sweep` routine clears these on its cadence. For an
immediate clear, run `/cadence:sweep` once or delete the label in Linear. The
threshold is `limits.stale_after_minutes` in `workflow.yaml` (default 30).

### Max attempts reached

**Symptom:** an issue is sidelined with `cadence-needs-human`; comments include
`[Cadence] Max attempts (N) reached at state X`.
**Cause:** the same workflow state failed `limits.max_attempts_per_issue` times
in a row (default 3); the bootstrap stopped auto-retrying.
**Fix:** read the failure records (`<!-- cadence:state {"status":"failed",...} -->`)
to find the root cause — missing credentials, impossible task, flaky test,
ambiguous description — address it, then remove `cadence-needs-human`. The
attempt counter is **not** reset by label removal; delete the prior
attempt-marker comments for a clean slate. To abandon an issue permanently,
move it to a column outside the workflow and leave the label set.

### Rework limit reached

**Symptom:** like max attempts, but the failure comment names a gate:
`[Cadence] Rework limit reached at gate <name>`.
**Cause:** the gate's `max_rework` was exceeded.
**Fix:** same shape as max attempts — read the rework comments, fix the
underlying problem (often a clearer review comment or smaller scope), remove
`cadence-needs-human`. Delete prior `<!-- cadence:gate {"status":"rework"} -->`
comments to reset the counter.

### Validation errors

**Symptom:** every `/cadence:tick` fire exits immediately with a config error;
no Linear writes happen.
**Cause:** `.claude/workflow.yaml` violates a validation rule (duplicate column,
undefined target, missing subagent file).
**Fix:** the error names the offending keys — fix the YAML. Run
`/cadence:tick dry-run` to confirm before going live again.

### Issue moved to an unmapped state

**Symptom:** a comment reads `Issue moved to unmapped Linear state <X> between
pickup and dispatch; releasing lock without action`, and the issue is
repeatedly picked up and released.
**Cause:** something moved the issue to a Linear column not in
`workflowLinearStates`.
**Fix:** move it back to a workflow column, add the column to `workflow.yaml`,
or remove its `cadence-active` label and accept it's out of band.

### Drift reconciliation

**Symptom:** a `<!-- cadence:reconcile ... -->` comment reads `Detected
human-driven state change; proceeding from Linear's state.`
**Cause:** a human dragged the issue between columns out of band; the bootstrap
detected the drift and is proceeding from Linear's authoritative state.
**Fix:** usually none — this is expected. If unintended, move it back and the
next fire re-reconciles.

### `/cadence:status` is slow

**Symptom:** status takes tens of seconds to render.
**Cause:** each issue's comment list is fetched separately to find its latest
attempt marker; large workflows amplify this.
**Fix:** the reporter degrades gracefully — it renders attempt counts as `?` on
fetch failure. To keep the table fast, narrow the project scope or run it
against a Linear filter view.

---

## Architecture in one glance

- **Linear state is the workflow state** — 1:1, no aliasing. The bootstrap
  reads Linear every fire to learn the current state.
- **Soft lock = a label** (`cadence-active`) — added at lock acquisition,
  removed at end of fire, stale ones swept on a separate cadence.
- **One issue per fire** — concurrency comes from cron frequency or parallel
  sessions, not intra-fire fan-out.
- **Subagents are stateless and Linear-blind** — the bootstrap is the sole
  Linear writer; subagents read code, make changes, open PRs, and return a
  Markdown string the bootstrap posts verbatim.
- **Plugin owns logic, consumer owns config** — the plugin ships the bootstrap
  prose, sweep / status logic, and subagent templates; the consumer owns
  everything under `.claude/`.

See [GUIDEPOSTS.md](./GUIDEPOSTS.md) for why the system is shaped this way.

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
