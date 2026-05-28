# CLAUDE.md

Orientation for an agent working **on the Cadence plugin itself**. For how
Cadence behaves once installed, read [README.md](./README.md).

## What Cadence is

A Claude Code plugin that turns Linear into a multi-agent workflow runner.
Issues flow through a state machine; subagents do the work; humans approve at
gates. There is no daemon — each tick is one shot, fired by `/schedule` or
`/loop`.

## Repo map

- `commands/` — the five slash commands (`tick`, `init`, `sweep`, `status`,
  `create-ticket`). Each `.md` is **dispatch prose the harness executes**,
  not documentation. `tick.md`, `sweep.md`, and `status.md` are also copied
  to the consumer's `.claude/commands/cadence/` at init so `/schedule`
  cloud routines can dispatch them without resolving a plugin root.
- `templates/` — **mirror of the consumer's `.claude/` tree.** Everything
  here is copied 1:1 to the same relative path under `.claude/` by
  `/cadence:init`: `workflow.yaml`, `prompts/global.md`, `ticket-template.md`,
  `agents/*.md`, `hooks/*.py`, `settings.json` (the last is merged into
  `.claude/settings.json` rather than copied verbatim).
- `templates/hooks/` — nine Python files copied to the consumer's
  `.claude/hooks/`. Three are PreToolUse / UserPromptSubmit / PostToolUse
  hooks (`validate_tracking_json.py`, `validate_workflow_on_prompt.py`,
  `audit_linear_writes.py`); six are deterministic helpers the dispatch
  prose invokes via Bash (`validate_workflow.py`, `_common.py`,
  `parse_comments.py`, `emit_tracking_comment.py`,
  `compose_lifecycle_context.py`, `filter_candidates.py`). All nine are
  always overwritten on init — they are plugin-owned executables, not
  user config.
- `scripts/` — plugin-only init-time helpers (`merge_settings_hooks.py`,
  `merge_settings_permissions.py`). Never scaffolded to the consumer; only
  invoked from `commands/init.md`. Contract documented in
  [`scripts/README.md`](./scripts/README.md).
- `.claude-plugin/plugin.json` — plugin manifest (name, version, metadata).
- `.github/workflows/validate.yml` — CI: manifest schema + command frontmatter.
- Root docs: `README.md` (operational shape), `GUIDEPOSTS.md` (design
  principles / the *why*), `CHANGELOG.md` (what shipped, in order),
  `BACKLOG.md` (ideas / deferred work), `MIGRATION.md` (Stokowski → Cadence).

## Load-bearing invariants — do not break these

- **`commands/tick.md` prose IS the dispatch logic.** Editing it is a spec
  change, not a doc edit. Step ordering and wording are behavior.
- **The bootstrap is the sole Linear writer.** Subagents read code, make
  changes, and return a Markdown string; the bootstrap posts it verbatim.
- **Linear column ↔ workflow state is 1:1.** No aliasing.
- **`commands/*.md` are invoked independently** as slash commands and must
  stay self-contained — do not factor shared prose into a file a command
  would have to `Read` at runtime.
- **Plugin owns logic, consumer owns config.** Don't bake consumer-specific
  assumptions into `commands/` or `templates/`.

## Conventions

- Commit messages: conventional-commit style, no AI-attribution trailer
  (match the existing `git log`).
- Every file in `commands/` needs YAML frontmatter with a `description` and
  `disable-model-invocation: true` — CI enforces this.
- `templates/` files are examples the consumer edits and commits; `commands/`
  files are the plugin's own executable prose. Keep the boundary clean.

## Where to look

| Need | Doc |
|---|---|
| How the system behaves when installed | `README.md` |
| Why it's designed this way | `GUIDEPOSTS.md` |
| What has shipped, in order | `CHANGELOG.md` |
| Deferred ideas / known gaps | `BACKLOG.md` |
| Helper-script contract (exit codes, stdin/stdout shape) | `scripts/README.md` |
