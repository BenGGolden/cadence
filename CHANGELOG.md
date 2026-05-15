# Changelog

All notable changes to Cadence are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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
- `.claude-plugin/plugin.json` — version bumped to `0.2.0`.
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
