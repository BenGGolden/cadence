# Changelog

All notable changes to Cadence are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added — Phase 4: label-based gate signalling
- `templates/workflow.example.yaml` `label:` section gains
  `cadence_approve` / `cadence_rework` entries — the two new
  config-defined verdict labels. A human signals their decision at a
  gate by adding one of these labels to an issue sitting in the gate's
  waiting column; the next `/cadence:tick` fire reads the label, routes
  the issue accordingly, and removes the label.
- `scripts/validate_workflow.py` Rule 8 — rejects any gate state still
  carrying the legacy `approved_linear_state` / `rework_linear_state`
  keys (removed in this phase) and points the operator at the
  CHANGELOG migration note.
- `scripts/merge_settings_permissions.py` — plugin-only helper invoked
  by `/cadence:init` to perform an idempotent merge of the canonical
  Cadence Linear MCP verb list into the consumer's
  `.claude/settings.local.json` `permissions.allow` array. Three known
  Linear MCP namespaces (`linear`, `linear-server`, `claude_ai_Linear`)
  are supported via a `--namespace` argument the caller derives from
  `claude mcp list` or `.mcp.json`. A `--print-only` mode emits the
  block without writing, for the "Next steps" output `/schedule`
  operators paste into the routine's permissions panel.

### Changed — Phase 4: label-based gate signalling
- `templates/workflow.example.yaml` — the `review` gate block drops
  `approved_linear_state` and `rework_linear_state`. A gate now
  declares a single Linear column (its waiting queue) plus
  `on_approve` / `on_rework` targets and optional `max_rework`. This
  collapses the per-gate Linear-column cost from three columns to one;
  a workflow with three gates drops from nine gate columns to three
  plus the two globally-shared verdict labels.
- `scripts/validate_workflow.py` Rule 1 — uniqueness now collects only
  `linear_state` values plus `linear.pickup_state`; the per-gate
  approved/rework fields are no longer part of the set.
  `_build_linear_states_set` and the `workflow_linear_states` JSON
  field are amended in lockstep.
- `commands/tick.md` — Vocabulary, Step 3 (validation prose), Step 4
  (`workflowLinearStates` construction), Step 8 (matched state lookup),
  and Step 9 (drift check Match rule) are updated for the single-column
  gate. Step 10 is rewritten: 10a (neither label), 10b
  (`cadence_approve` present → route to `on_approve`), 10c
  (`cadence_rework` present → route to `on_rework`), plus a defensive
  "both labels present" branch that falls back to rework. The bootstrap
  removes the verdict label after acting.
- `commands/status.md` — the reverse-lookup map drops `gate_approved`
  and `gate_rework`; the table gains a **Verdict** column showing which
  verdict label (if any) is queued on each gate-waiting row; the
  per-state summary's gate rendering replaces the three-line breakdown
  with one waiting-column line plus a verdict-label breakdown.
- `commands/init.md` — adds **step 4c** (detect Linear MCP namespace +
  invoke `merge_settings_permissions.py`); step 5's "Next steps"
  output adds a **Gate labels** block (reminding the operator to create
  the two labels and recommending the label group) and a
  **Permissions for /schedule routines** block (the verb list in
  copy-pasteable form for routines, which do not read
  `.claude/settings.local.json`).
- `README.md` — Consumer setup section's "Required Linear columns" list
  drops `Approved` / `Needs Rework`; the sample `/cadence:status` output
  reflects the single-column gate plus Verdict column; a callout under
  "Linear MCP tools" notes that `/cadence:init` automates the local
  allowlist write but `/schedule` routines still need the manual paste.
- `MIGRATION.md` — Stokowski schema example drops the two legacy gate
  keys; adds an **Upgrading to label-based gates** section walking
  through the in-place migration.
- `.claude-plugin/plugin.json` — version bumped to `0.4.0`.

### Removed — Phase 4: label-based gate signalling
- `approved_linear_state` and `rework_linear_state` from the gate
  schema. `validate_workflow.py` Rule 8 rejects configs still carrying
  them; tick / status / docs no longer reference them.

### Added
- `templates/ticket-template.md` — paste-able skeleton an operator drops
  into Linear's "Description" field for a new ticket. Scaffolded into the
  consumer's `.claude/ticket-template.md` by `/cadence:init` (hardening
  plan, Phase 3).
- `commands/create-ticket.md` — new `/cadence:create-ticket` slash
  command that walks the operator through filling the template
  interactively and emits a paste-ready Markdown block. No Linear writes;
  no subagent invocation. Validates that each acceptance-criterion is
  specific enough to verify from the diff and the test suite.
- `templates/agents/planner.md` — adds a "## Ticket-quality gate" section.
  The planner now refuses to plan a ticket whose description lacks a
  `## Acceptance Criteria` H2 with at least one `- [ ] **AC-N** —`
  checkbox item, returning a fixed "Cannot plan" summary that the
  bootstrap posts as a Linear comment and counts toward
  `max_attempts_per_issue` (escalating to `cadence-needs-human` after the
  configured number of fires).
- `templates/agents/implementer.md` — adds an "### Acceptance criteria"
  block to the required return-summary shape. The implementer must walk
  the ticket's `## Acceptance Criteria` list and either tick each item
  with a `Verified by:` artefact, or leave it unticked with a
  `Not addressed because:` reason.
- `templates/agents/reviewer.md` — adds step 0 to "How to review": locate
  the ticket's `## Acceptance Criteria` block and verify the
  implementer's per-AC claims against the actual diff; a `[x]` whose
  verification artefact does not exist is a blocking finding.
- `scripts/` — three deterministic Python helper scripts plus a shared
  `_common.py` module, invoked from command prose via `Bash`
  (hardening plan, Phase 1):
  - `validate_workflow.py` — enforces the five `.claude/workflow.yaml`
    config rules in code; `--evidence` emits per-rule evidence for the
    dry-run report.
  - `parse_comments.py` — deterministic counting and classification of a
    Linear issue's tracking comments (attempt count, rework count, rework
    context, latest tracking comment).
  - `emit_tracking_comment.py` — produces canonical tracking-comment
    bodies so the embedded JSON is always well-formed.
- `templates/hooks/` — three Claude Code hook scripts, scaffolded into the
  consumer's `.claude/hooks/` directory by `/cadence:init` (hardening plan,
  Phase 2):
  - `validate_tracking_json.py` — `PreToolUse` hook that blocks Linear
    comment-create calls whose Cadence tracking-comment JSON does not parse.
  - `validate_workflow_on_prompt.py` — `UserPromptSubmit` hook that runs
    `validate_workflow.py` before `/cadence:tick` proceeds, blocking the
    prompt on a broken `.claude/workflow.yaml`.
  - `audit_linear_writes.py` — `PostToolUse` hook that appends one JSONL
    line per Linear write to `.cadence/audit.log`, for forensic debugging
    of a fire after the fact.
- `templates/settings.example.json` — canonical Cadence hooks block,
  merged into the consumer's `.claude/settings.json` by `/cadence:init`.
  Matchers are namespace-flexible regexes that catch any Linear MCP tool
  whose server namespace contains `linear` / `Linear` (e.g.
  `mcp__linear__*`, `mcp__linear-server__*`, `mcp__claude_ai_Linear__*`),
  plus bare-named variants. Unrelated MCP servers (GitHub, Notion, etc.)
  are excluded.
- `scripts/merge_settings_hooks.py` — plugin-only helper invoked by
  `/cadence:init` to perform an idempotent merge of the hooks block into
  the consumer's `.claude/settings.json`. Non-Cadence hook entries are
  preserved; existing Cadence entries are replaced rather than duplicated.

### Changed
- `commands/tick.md` — steps 0, 3, 4, 9, 10c, 11, 12, 16 and the Failure
  path now delegate config validation, comment counting, and tracking-comment
  emission to the `scripts/` helpers instead of doing the bookkeeping in LLM
  prose.
- `commands/sweep.md` and `commands/status.md` — now call
  `validate_workflow.py` for config validation; `status.md` also uses
  `parse_comments.py` for per-issue attempt counts.
- `commands/init.md` — creates `.claude/hooks/`, copies the three hook
  scripts plus `validate_workflow.py` and `_common.py` alongside them, and
  merges Cadence's hook entries into `.claude/settings.json`.
- `commands/init.md` — also copies `parse_comments.py` and
  `emit_tracking_comment.py` into `.claude/hooks/`, and copies the
  dispatch prose `commands/{tick,sweep,status}.md` into
  `.claude/commands/cadence/`. `commands/{tick,sweep,status}.md` now
  reference helper scripts via `$CLAUDE_PROJECT_DIR/.claude/hooks/...`
  rather than `${CLAUDE_PLUGIN_ROOT}/scripts/...`. This makes
  `/cadence:tick` runnable from a `/schedule` cloud routine, which
  previously had no path to find either the dispatch prose or the
  helper scripts (the plugin is not installed in the cloud session).
- `commands/init.md` — also copies `templates/ticket-template.md` to
  `.claude/ticket-template.md`, lists it in the overwrite-check block
  and the "Files written" output, and appends `/cadence:create-ticket`
  guidance to "Next steps" (hardening plan, Phase 3).
- `commands/tick.md` step 9 (drift detection) — no longer treats normal
  agent→agent forward progression as drift. After a successful fire that
  advanced Linear from state X to state X.next (an agent state), the next
  fire would see `latest_tracking_comment.state = X` and matched state =
  X.next and incorrectly post a reconcile comment claiming a human
  reassignment. Step 9 now treats `matched == config.states[latest.state].next`
  as the expected pattern. Gate transitions were already handled
  correctly because step 16's gate-next branch emits a fresh tracking
  comment when advancing into a gate; only the agent→agent path lacked
  the corresponding update, which the smarter drift check now compensates
  for without adding extra comments.
- `commands/tick.md` step 5 — added explicit query-shape requirements:
  pass `linear.project_slug` to the MCP project filter verbatim, do not
  transform it, and do not fall back to broader queries (team-only, ID
  lookups) if the filtered query returns empty. Without this, the
  bootstrap LLM would sometimes improvise a fallback that masked a
  misconfigured `project_slug`, producing inconsistent fire-to-fire
  behavior.
- `templates/workflow.example.yaml` — `project_slug` comment corrected
  to specify the project's name or UUID, and warn against using the URL
  suffix (Linear URLs end in `<name>-<hash>`, but the hashed form is not
  accepted as an MCP filter value).
- `linear.project_slug` is now **optional**. Absent → `/cadence:tick`,
  `/cadence:sweep`, and `/cadence:status` query team-wide; present →
  the query narrows to that project (existing behaviour). Linear's data
  model treats team as primary and project as an optional facet, so
  forcing a project meant operators either created a project they did
  not otherwise want or silently got zero pickup hits for issues that
  were not assigned to the configured project. Touches
  `templates/workflow.example.yaml` (placeholder commented out, doc
  rewritten as optional), `commands/tick.md` step 5, `commands/sweep.md`
  steps 1 and 3, and `commands/status.md` steps 1, 3, and 5. The
  validator already did not enforce `project_slug`, so no script change
  was needed.
- `commands/{tick,sweep,status}.md` — every helper-script Bash
  invocation now uses `"${CLAUDE_PROJECT_DIR:-.}"/.claude/hooks/...`
  instead of `"$CLAUDE_PROJECT_DIR"/.claude/hooks/...`. The variable is
  reliably set in hook subprocess environments (which
  `templates/settings.example.json` still depends on) but not always in
  the Bash tool environment of a local Claude Code session — when
  unset, Git Bash on Windows expanded the leading `/` against its own
  install root (`C:\Program Files\Git\.claude\hooks\...`) and the
  script failed to open. The fallback resolves to cwd `.`, which the
  harness keeps at the project root.
- `.claude-plugin/plugin.json` — version bumped to `0.3.0`.
- `README.md` — "Linear MCP tools" section now opens with a namespace
  primer (`mcp__linear__*` vs. `mcp__linear-server__*` vs.
  `mcp__claude_ai_Linear__*` vs. bare), and the read/write allowlist
  tables include the `mcp__linear-server__*` variants. Notes that
  Claude Code's permission allowlist matches by exact tool name (unlike
  the shipped hook regex), so operators must substitute the namespace
  their MCP server actually exposes.

## [0.1.0] — 2026-05-11

Initial scaffolding release. The plugin compiles, all four slash commands
exist and are implemented end-to-end in prose. End-to-end validation
against a live Linear project is tracked in [SMOKE.md](./SMOKE.md) and
happens per consuming repo.

### Added
- Plugin manifest at `.claude-plugin/plugin.json`.
- `/cadence:init` — scaffolds `.claude/workflow.yaml`, `.claude/agents/*.md`,
  and `.claude/prompts/global.md` into the consumer repo.
- `/cadence:tick` — single-shot bootstrap that picks one Linear issue,
  acquires a soft lock, reconciles state, dispatches the matching subagent,
  posts a tracking comment, and exits.
- `/cadence:sweep` — clears stale `cadence-active` labels left behind by
  killed `/schedule` fires.
- `/cadence:status` — read-only human-facing status view of all issues in
  the workflow.
- Starter subagent templates under `templates/agents/`
  (planner / implementer / reviewer).
- Workflow YAML and global-prompt examples under `templates/`.
- Stokowski → Cadence migration guide ([MIGRATION.md](./MIGRATION.md)).
- Smoke-test checklist ([SMOKE.md](./SMOKE.md)).
