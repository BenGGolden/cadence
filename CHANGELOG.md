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

### Changed
- `commands/tick.md` — steps 0, 3, 4, 9, 10c, 11, 12, 16 and the Failure
  path now delegate config validation, comment counting, and tracking-comment
  emission to the `scripts/` helpers instead of doing the bookkeeping in LLM
  prose.
- `commands/sweep.md` and `commands/status.md` — now call
  `validate_workflow.py` for config validation; `status.md` also uses
  `parse_comments.py` for per-issue attempt counts.

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
