# Cadence

A Claude Code plugin that turns Linear into a multi-agent workflow runner.
Issues flow through a state machine you define; subagents do the work;
humans approve at gates; PRs land. No long-running daemon — each tick is
one shot, fired by `/schedule` or `/loop`.

Cadence is a reusable, packaged replacement for the per-project
[Stokowski](https://github.com/Sugar-Coffee/stokowski) supervisor. Consuming
projects install the plugin, run `/cadence:cadence-init`, edit one YAML
file and three subagent prompts, point a scheduled routine at
`/cadence:cadence-tick`, and watch Linear.

> **Build status:** Session A scaffolding is complete (this commit). The
> dispatch tick `/cadence:cadence-tick` is a stub until Session B; the
> sweeper and status reporter are stubs until Session C. The plugin
> installs cleanly and `/cadence:cadence-init` is fully functional today.
> See [PLAN.md](./PLAN.md) for the full build plan.

---

## What it does

```
   /schedule or /loop fires on cron interval
        |
        v
   /cadence:cadence-tick (Cadence bootstrap)
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
behaviour is described in prose in `commands/cadence-tick.md` — that
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
| `commands/cadence-tick.md`    | `/cadence:cadence-tick`          |
| `commands/cadence-init.md`    | `/cadence:cadence-init`          |
| `commands/cadence-sweep.md`   | `/cadence:cadence-sweep`         |
| `commands/cadence-status.md`  | `/cadence:cadence-status`        |

[marketplaces]: https://code.claude.com/docs/en/plugin-marketplaces

---

## Consumer setup

Both invocation modes use the same plugin and the same
`/cadence:cadence-tick` command. They differ in where the cron lives.

### Mode A — Remote (`/schedule`)

Fully autonomous. No operator presence required between fires.

```bash
# 1. Install plugin (see above).

# 2. From the consuming repo's Claude Code session:
/cadence:cadence-init

# 3. Edit the scaffolded files:
#    .claude/workflow.yaml        ← Linear team, project, state names
#    .claude/agents/planner.md    ← model, tools, system prompt
#    .claude/agents/implementer.md
#    .claude/agents/reviewer.md
#    .claude/prompts/global.md    ← shared preamble

# 4. Create a /schedule routine:
#       Schedule:  */1 * * * *
#       Repo:      this repo
#       Prompt:    /cadence:cadence-tick
#       MCP:       Linear MCP server
#       Env:       GH_TOKEN (for gh CLI in the implementer)

# 5. Create a second /schedule routine for stale-lock cleanup:
#       Schedule:  */15 * * * *
#       Prompt:    /cadence:cadence-sweep

# 6. Watch Linear.
```

### Mode B — Local (`/loop`)

Operator-tended. Runs in a local Claude Code session in the repo.

```bash
# Steps 1-3 identical to Mode A.

# 4. From an interactive Claude Code session in the repo, after gh auth login:
claude /loop 1m /cadence:cadence-tick

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

`/cadence:cadence-init` creates the following in the consumer's repo:

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

Re-running `/cadence:cadence-init` refuses to overwrite. Use
`/cadence:cadence-init --force` to replace existing files.

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
