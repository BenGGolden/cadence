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

> **Build status:** v1 scaffolding is complete. All five slash commands
> (`/cadence:init`, `/cadence:tick`, `/cadence:sweep`, `/cadence:status`,
> `/cadence:create-ticket`) are implemented. Per-phase smoke checks for
> the in-flight hardening work live alongside their acceptance criteria
> in [HARDENING-PLAN.md](./HARDENING-PLAN.md).

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
        +-- 4. For gates: read verdict labels (approve / rework / waiting)
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

Once loaded, the five slash commands appear under the `cadence:` namespace:
`/cadence:tick`, `/cadence:init`, `/cadence:sweep`, `/cadence:status`,
`/cadence:create-ticket`.

[marketplaces]: https://code.claude.com/docs/en/plugin-marketplaces

---

## Consumer setup

Both invocation modes use the same plugin and the same `/cadence:tick`
command — they differ only in where the cron lives. You need: Claude Code with
plugin support, a Linear MCP server, GitHub auth for the implementer subagent
(`gh auth login` locally or `GH_TOKEN` on the routine), and a Linear board with
one column per workflow stage. The default workflow needs seven columns —
Todo, Planning, Plan Review, Implementing, Reviewing, In Review, Done —
reshape via `workflow.yaml`. (`Plan Review` and `Reviewing` are new as of
the plan-review / agent-review phase; add them before upgrading an existing
board — see [CHANGELOG.md](./CHANGELOG.md).)

Gates use **one column plus two labels**, not three columns. Create the
`cadence-approve` and `cadence-rework` labels in Linear alongside the
existing `cadence-active` / `cadence-needs-human` labels; a reviewer
signals their verdict on an issue sitting in the gate's waiting column
(`In Review` in the default workflow) by adding one of those labels.
**Recommended:** put both labels into a Linear label group (workspace
settings → Labels → New group) so the picker renders the verdict as a
single-select control instead of two independent toggles. Cadence treats
the dual-label case as rework as a defensive guard, but the group makes
it structurally unreachable from the UI.

### Mode A — Remote (`/schedule`)

Fully autonomous. No operator presence required between fires.

1. Install the plugin locally (see above). Cloud `/schedule` routines do
   **not** load Claude Code plugins — plugins are local-only. Cadence works
   around this by having `/cadence:init` copy the dispatch prose
   (`.claude/commands/cadence/{tick,sweep,status}.md`) and helper scripts
   (`.claude/hooks/*.py`) into the consumer repo, so the cloud session
   reads them as project-scoped slash commands. The local install only
   exists to run `/cadence:init` once and to drive `/loop` if you also
   want Mode B.
2. Run `/cadence:init` in the consuming repo's Claude Code session.
3. Edit the scaffolded files under `.claude/` — `workflow.yaml` (Linear team,
   project, state names), `agents/*.md` (model, tools, system prompt),
   `prompts/global.md` (shared preamble). Commit the whole `.claude/`
   directory — the cloud routine reads the dispatch prose, hooks, and
   `settings.json` from the checked-out repo.
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

#### GitHub CLI setup

The implementer subagent opens PRs via `gh`, which isn't preinstalled
on the routine image. Without it, the implementer falls back to
"branch pushed; PR creation skipped" — workable, but you lose
automatic PR links in the Linear summary.

The fix is one line in the routine's **Setup script** field
(environment settings, runs once at environment build and is cached
across fires — not the routine prompt):

```bash
#!/bin/bash
apt update && apt install -y gh
```

Routine sandboxes run as root on Ubuntu 24.04, so no `sudo` is
needed. Pair the install with `GH_TOKEN` on the routine env (already
covered in step 5 above) — `gh` reads `GH_TOKEN` automatically and
auths headlessly. No `gh auth login` flow required.

If you skip this and `gh` is missing at fire time, the implementer
template is designed to bail cleanly rather than improvise — see the
[hardening plan's Phase 9](./HARDENING-PLAN.md#phase-9--subagent-scope-discipline--bootstrap-silence)
for the discipline.

### Ticket quality

Cadence treats every Linear issue as a contract. The planner subagent
refuses to plan a ticket whose description does not contain an
`## Acceptance Criteria` H2 with at least one
`- [ ] **AC-N** — <specific outcome>` checkbox item — those refusals
count toward `max_attempts_per_issue` and eventually escalate with the
`cadence-needs-human` label.

To draft well-formed tickets, run `/cadence:create-ticket` in your local
Claude Code session. It walks you through the template at
`.claude/ticket-template.md`, validates each AC against a vagueness
heuristic, and emits a paste-ready Markdown blob you drop into Linear's
"New Issue" form. The command does not touch Linear directly — keeping
local sessions free of any Linear MCP requirement — and does not invoke
any subagent.

For tickets created outside this flow (in Linear's UI, or imported from
another tracker), paste the contents of `.claude/ticket-template.md` into
the description and fill in the sections; the planner's quality bar is
the same either way.

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

## Workflow tuning

### `max_in_flight` — per-state concurrency caps

Any `type: agent` or `type: gate` state in `.claude/workflow.yaml` can
declare an optional `max_in_flight: N` (positive integer). When set,
`/cadence:tick` counts the issues currently sitting in that state's
`linear_state` column on every fire and walks each candidate's
happy-path downstream (`next` for agents, `on_approve` for gates) at
pickup time; if any state on the walk is at its cap, the candidate is
skipped and the fire exits with a `(caps reached for: …)` note. The cap
is **coordination, not a hard lock** — counts are recomputed from live
Linear column membership each fire, so manual moves between fires
self-correct on the next pickup.

**Agent caps vs. gate caps** — they look the same in YAML but bind
differently:

- An **agent cap** throttles **parallel subagent runs** at the agent's
  own state. Useful for limiting how many planners or implementers
  fire in parallel.
- A **gate cap** throttles the gate's **waiting queue** by blocking
  candidates whose happy-path downstream feeds the gate. Useful for
  capping the depth of work a reviewer is asked to triage. The
  bootstrap exempts verdict-bearing issues already sitting in the
  gate from their own gate's cap — acting on a verdict drains the
  queue, so the gate's own cap must not block the drain.

For controlling pile-up in `plan_review` / `human_review`, the gate
cap is the right tool — it caps the queue directly. An upstream agent
cap only narrows the inflow and stops binding the moment the agent's
own column drains, even if the downstream gate is overflowing.

Caps are forbidden on `type: terminal` states (terminals have no
pickup to throttle). The validator (Rule 6) rejects that shape.
`/cadence:status` surfaces current cap usage in its Concurrency table
when any state declares one — gates with caps get a row alongside the
agent states.

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
| `Bash`      | Generate the current UTC timestamp for tracking-comment JSON (`date -u …` or `Get-Date …`), and run the Python helper scripts under the plugin's `scripts/` directory (config validation, comment parsing, tracking-comment emission). |
| `Agent`     | Invoke planner / implementer / reviewer subagents.           |
| `TodoWrite` | Optional — only if you want progress visibility on long fires. |

### Hooks (scaffolded into `.claude/hooks/` by `/cadence:init`)

`/cadence:init` writes three Claude Code hook scripts under `.claude/hooks/`
and merges the matching entries into `.claude/settings.json`:

| Hook                              | Event              | Why                                                                                                                       |
|-----------------------------------|--------------------|---------------------------------------------------------------------------------------------------------------------------|
| `validate_tracking_json.py`       | `PreToolUse`       | Blocks any Linear comment-create whose `<!-- cadence:* -->` tracking-comment JSON does not parse, before it reaches Linear. |
| `validate_workflow_on_prompt.py`  | `UserPromptSubmit` | Runs `validate_workflow.py` when a `/cadence:tick` prompt is submitted, blocking the run on a broken `.claude/workflow.yaml`. |
| `audit_linear_writes.py`          | `PostToolUse`      | Appends one JSON-per-line entry to `.cadence/audit.log` for every Linear write the fire made.                              |

The hooks are scoped: each script no-ops immediately if
`.claude/workflow.yaml` is absent, so leaving them installed in a repo that
no longer uses Cadence does no harm.

The audit log lives at `.cadence/audit.log`. In `/loop` mode it accumulates
across fires; in `/schedule` mode it is fresh per fire (the routine works
on a clone that is discarded when the session ends) and is most useful for
debugging the current session via claude.ai/code/sessions. The hook creates
`.cadence/.gitignore` containing `*` on first write, so the audit log is
never accidentally committed.

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

**A note on namespaces.** Claude Code MCP tool names take the form
`mcp__<server-name>__<tool-name>`, where `<server-name>` is whatever the
operator passed to `claude mcp add` (or named in `.mcp.json`, or what the
claude.ai connector exposes). Three namespaces show up in the wild for
Linear:

- `mcp__linear__*` — the official Linear MCP server when installed under
  the name `linear`.
- `mcp__linear-server__*` — same server, installed under the name
  `linear-server` (common on Windows installs that follow Linear's docs).
- `mcp__claude_ai_Linear__*` — the claude.ai workspace connector.

Plus the bare names (`save_comment`, `get_issue`, etc.) some bridges
expose without an `mcp__<server>__` prefix.

The shipped Cadence hook matchers in `templates/settings.example.json`
catch all of these via the regex pattern
`mcp__[A-Za-z0-9_-]*[Ll]inear[A-Za-z0-9_-]*__<tool>` — any namespace
containing `linear` or `Linear`. **The Claude Code permission allowlist
does not.** Pre-allow rules are evaluated against exact tool names, so
the tables below are illustrative — substitute the names your specific
MCP server actually exposes. Check `claude mcp list` and look at the
permission prompt the first time a Cadence subagent tries to read or
write Linear.

> **`/cadence:init` automates this for local sessions.** Step 4c
> detects your Linear MCP namespace and writes the canonical Cadence
> allowlist into `.claude/settings.local.json` so you don't have to
> paste it yourself. **Cloud `/schedule` routines do NOT read
> `.claude/settings.local.json`** — `/cadence:init`'s "Next steps"
> output also prints the same block under a "Permissions for /schedule
> routines" heading for you to paste into the routine's permissions
> panel.

**Read tools — pre-allow only what Cadence calls.**
The prose reaches for exactly three read-only tools. Set these to
Always Allow (using whichever namespace prefix your server exposes):

| Intent          | Example names — substitute your namespace                                                          |
|-----------------|----------------------------------------------------------------------------------------------------|
| List issues     | `list_issues`, `mcp__linear__list_issues`, `mcp__linear-server__list_issues`, `search_issues`      |
| Read an issue   | `get_issue`, `mcp__linear__get_issue`, `mcp__linear-server__get_issue`                             |
| List comments   | `list_comments`, `mcp__linear__list_comments`, `mcp__linear-server__list_comments`                 |

Leave the rest of the read-only category on Ask. "Read-only" is not
"harmless" — `get_document`, `list_users`, `list_projects`, etc. read
confidential data that has no business in a subagent's context, and a
hallucinated call could echo it into a comment the bootstrap posts verbatim.
Bulk-allowing the whole category is fine in a throwaway test workspace; on any
workspace with real data, pre-allow narrowly.

**Write tools — pre-allow only what Cadence calls.**
Set these to Always Allow (using whichever namespace prefix your server
exposes):

| Intent                       | Example names — substitute your namespace                                                                                          |
|------------------------------|------------------------------------------------------------------------------------------------------------------------------------|
| Post a comment               | `save_comment`, `mcp__linear__create_comment`, `mcp__linear-server__save_comment`                                                  |
| Update issue (state, labels) | `save_issue`, `mcp__linear__update_issue`, `mcp__linear-server__save_issue`, `mcp__linear__add_label`, `mcp__linear__remove_label` |

If your MCP server uses a namespace whose prefix does **not** contain
"linear" (case-insensitive), you also need to extend the matcher regex in
`.claude/settings.json` so the Cadence hooks see those tool calls — the
shipped regex assumes `linear` appears somewhere in the server name.

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

Sample output for a small workflow with several live issues:

```markdown
## Cadence status — 2026-05-11T14:23:01Z

Team: **ENG**   Project: **acme-platform**   Pickup: **Todo**

### Issues in workflow

| ID      | Title                                              | Linear column | Workflow state    | Attempt | Lock | Needs human | Verdict          |
|---------|----------------------------------------------------|---------------|-------------------|---------|------|-------------|------------------|
| ENG-204 | Add OAuth callback retry on transient 5xx          | Implementing  | implement         | 2       | 🔒   |             |                  |
| ENG-198 | Migrate analytics worker to BullMQ                 | In Review     | review (waiting)  | 1       |      |             |                  |
| ENG-187 | Crash on empty rate-limit header                   | In Review     | review (waiting)  | 2       |      |             | cadence-rework   |
| ENG-176 | Tighten auth middleware regex                      | Todo          | (pickup)          | —       |      |             |                  |
| ENG-149 | Reindex legacy events                              | Implementing  | implement         | 3       |      | 🛑          |                  |

### Per-state counts

- **(pickup)** (`Todo`) — 1 issues
- **plan** (`Planning`) — 0 issues
- **implement** (`Implementing`) — 2 issues   🔒 1 locked   🛑 1 needs-human
- **review** (gate, `In Review`) — 2 issues
  - awaiting verdict — 1 issues
  - 👎 cadence-rework — 1 issues
- **done** (`Done`) — 0 issues

Read-only — no Linear writes performed.
```

The **Verdict** column shows which gate-verdict label (if any) is queued
on each gate-waiting row. ENG-187 above will be routed back to
`implement` on the next `/cadence:tick` fire; the bootstrap will then
remove the label.

The lock (🔒) and needs-human (🛑) columns are the operational signals.
A lock means a tick is in flight (or a stale lock the sweeper hasn't
caught yet). Needs-human means the issue hit the attempt cap or rework
cap and is now sidelined until a human removes the
`cadence-needs-human` label. The Verdict column is the gate signal —
a value there means a reviewer has decided and the next fire will
route the issue accordingly.

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
    ├── ticket-template.md        # Cadence ticket skeleton — paste into Linear
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
