---
description: Scaffold Cadence workflow config into the consumer repo — creates .claude/workflow.yaml, .claude/agents/*.md, .claude/prompts/global.md from plugin templates.
argument-hint: "[--force]"
disable-model-invocation: true
---

# /cadence:init

You are the Cadence scaffolder. Run exactly once and exit. Your job is to
populate the consuming repository's `.claude/` directory with the files
Cadence's bootstrap (`/cadence:tick`) expects to find on every fire.

The argument may be `--force` (case-insensitive) or absent. Capture it
before you begin. Treat any other argument as invalid input — print an
error and exit.

## Step 1 — Confirm working directory

You are running from the consumer's repository root. The plugin templates
live under `${CLAUDE_PLUGIN_ROOT}/templates/` and the agent templates under
`${CLAUDE_PLUGIN_ROOT}/templates/agents/{planner,implementer,reviewer}.md`.
The plugin's own dispatch prose (`commands/tick.md`, `commands/sweep.md`,
`commands/status.md`) and helper scripts under `${CLAUDE_PLUGIN_ROOT}/scripts/`
are also copied into the consumer repo so that `/cadence:tick` is reachable
from a `/schedule` cloud routine (which never sees the local plugin install).
Do NOT write anywhere outside the current working directory.

## Step 2 — Overwrite check

Check whether `.claude/workflow.yaml` already exists.

- **If it exists AND `--force` was NOT supplied**: print this and exit
  without writing anything:

  ```
  Cadence is already initialized: .claude/workflow.yaml exists.

  Re-run with --force to overwrite. This will replace:
    - .claude/workflow.yaml
    - .claude/prompts/global.md
    - .claude/ticket-template.md
    - .claude/agents/planner.md
    - .claude/agents/implementer.md
    - .claude/agents/reviewer.md
    - .claude/hooks/validate_tracking_json.py
    - .claude/hooks/validate_workflow_on_prompt.py
    - .claude/hooks/audit_linear_writes.py
    - .claude/hooks/validate_workflow.py
    - .claude/hooks/_common.py
    - .claude/hooks/parse_comments.py
    - .claude/hooks/emit_tracking_comment.py
    - .claude/commands/cadence/tick.md
    - .claude/commands/cadence/sweep.md
    - .claude/commands/cadence/status.md
  Cadence's hook entries in .claude/settings.json will also be re-merged
  (non-Cadence entries are preserved).
  ```

- **If it does not exist, or `--force` was supplied**: proceed to step 3.

## Step 3 — Create directories

Create (if not already present):

- `.claude/`
- `.claude/agents/`
- `.claude/prompts/`
- `.claude/hooks/`
- `.claude/commands/cadence/`

## Step 4 — Copy templates

Use the Read tool to load each source file, then Write tool to create the
destination. Source paths use `${CLAUDE_PLUGIN_ROOT}` — substitute the
actual plugin root at runtime.

| Source                                                            | Destination                  |
|-------------------------------------------------------------------|------------------------------|
| `${CLAUDE_PLUGIN_ROOT}/templates/workflow.example.yaml`           | `.claude/workflow.yaml`      |
| `${CLAUDE_PLUGIN_ROOT}/templates/global-prompt.example.md`        | `.claude/prompts/global.md`  |
| `${CLAUDE_PLUGIN_ROOT}/templates/ticket-template.md`              | `.claude/ticket-template.md` |
| `${CLAUDE_PLUGIN_ROOT}/templates/agents/planner.md`               | `.claude/agents/planner.md`  |
| `${CLAUDE_PLUGIN_ROOT}/templates/agents/implementer.md`           | `.claude/agents/implementer.md` |
| `${CLAUDE_PLUGIN_ROOT}/templates/agents/reviewer.md`              | `.claude/agents/reviewer.md` |
| `${CLAUDE_PLUGIN_ROOT}/templates/hooks/validate_tracking_json.py` | `.claude/hooks/validate_tracking_json.py` |
| `${CLAUDE_PLUGIN_ROOT}/templates/hooks/validate_workflow_on_prompt.py` | `.claude/hooks/validate_workflow_on_prompt.py` |
| `${CLAUDE_PLUGIN_ROOT}/templates/hooks/audit_linear_writes.py`    | `.claude/hooks/audit_linear_writes.py` |
| `${CLAUDE_PLUGIN_ROOT}/scripts/validate_workflow.py`              | `.claude/hooks/validate_workflow.py` |
| `${CLAUDE_PLUGIN_ROOT}/scripts/_common.py`                        | `.claude/hooks/_common.py`   |
| `${CLAUDE_PLUGIN_ROOT}/scripts/parse_comments.py`                 | `.claude/hooks/parse_comments.py` |
| `${CLAUDE_PLUGIN_ROOT}/scripts/emit_tracking_comment.py`          | `.claude/hooks/emit_tracking_comment.py` |
| `${CLAUDE_PLUGIN_ROOT}/commands/tick.md`                          | `.claude/commands/cadence/tick.md` |
| `${CLAUDE_PLUGIN_ROOT}/commands/sweep.md`                         | `.claude/commands/cadence/sweep.md` |
| `${CLAUDE_PLUGIN_ROOT}/commands/status.md`                        | `.claude/commands/cadence/status.md` |

The agent templates already carry their final `name:` (`planner`,
`implementer`, `reviewer`) in their frontmatter — copy them verbatim. The
consumer's `workflow.yaml` references these short names.

The seven files copied into `.claude/hooks/` and the three files copied
into `.claude/commands/cadence/` are always overwritten on init (including
without `--force`). They are plugin-owned executables and dispatch prose,
not user config; keeping them in sync with the installed plugin is the
point. `validate_workflow.py`, `_common.py`, `parse_comments.py`, and
`emit_tracking_comment.py` are siblings under `.claude/hooks/` so that the
`UserPromptSubmit` hook and the copied `/cadence:*` commands can call them
via `$CLAUDE_PROJECT_DIR/.claude/hooks/...` without resolving a plugin path
at runtime — which is what makes the workflow runnable from a `/schedule`
cloud routine (where `${CLAUDE_PLUGIN_ROOT}` is not defined because the
plugin is not installed in the cloud session).

If `--force` was supplied and a destination already exists, overwrite it.
If `--force` was NOT supplied (which means step 2 fell through because
`.claude/workflow.yaml` was absent), still avoid clobbering any of the
agent or prompt destinations that happen to exist already — print a warning
naming each one you skipped, but continue with the rest. The seven files
under `.claude/hooks/` and the three files under `.claude/commands/cadence/`
are always copied regardless (see paragraph above).

## Step 4b — Merge hook entries into .claude/settings.json

After the file copies succeed, run this command via Bash to merge Cadence's
hook entries into the consumer's `.claude/settings.json` (creating it if
absent):

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/merge_settings_hooks.py" \
  --settings-path .claude/settings.json \
  --template-path "${CLAUDE_PLUGIN_ROOT}/templates/settings.example.json"
```

The merge is idempotent: re-running on a settings file that already contains
Cadence's hook entries replaces them rather than duplicating, and any
non-Cadence hook entries the consumer added are left untouched.

If the script exits non-zero, print its stderr and stop — partial
scaffolding is acceptable (file copies have already happened), and the
consumer can fix the underlying problem (usually a hand-broken
`.claude/settings.json`) and re-run with `--force`.

## Step 5 — Print next steps

After all writes succeed, print this verbatim:

```
Cadence initialised.

Files written:
  .claude/workflow.yaml
  .claude/prompts/global.md
  .claude/ticket-template.md
  .claude/agents/planner.md
  .claude/agents/implementer.md
  .claude/agents/reviewer.md
  .claude/hooks/validate_tracking_json.py
  .claude/hooks/validate_workflow_on_prompt.py
  .claude/hooks/audit_linear_writes.py
  .claude/hooks/validate_workflow.py
  .claude/hooks/_common.py
  .claude/hooks/parse_comments.py
  .claude/hooks/emit_tracking_comment.py
  .claude/commands/cadence/tick.md
  .claude/commands/cadence/sweep.md
  .claude/commands/cadence/status.md
  .claude/settings.json (Cadence hook entries merged in)

Next steps:
  1. Edit .claude/workflow.yaml to point at your Linear team/project and
     set the Linear state names that map to each workflow stage. Every
     linear_state value must correspond to a real column on your Linear
     board.
  2. Edit .claude/prompts/global.md with the always-on instructions you
     want every Cadence subagent to receive (coding standards, repo
     conventions, secrets-handling rules).
  3. Tune .claude/agents/{planner,implementer,reviewer}.md — model, tools,
     and system prompt.
  4. Pick an invocation mode:
       • Remote: create a /schedule routine running /cadence:tick
         every minute, with Linear MCP and GH_TOKEN configured on it. Add
         a second routine for /cadence:sweep every 15 minutes.
       • Local: from an interactive Claude Code session in this repo, run
         `claude /loop 1m /cadence:tick` (after `gh auth login`).
  5. Smoke test with /cadence:tick dry-run before going live.

To create well-formed tickets, run `/cadence:create-ticket` in your
local Claude Code session and paste the output into Linear's New
Issue form. The planner subagent will refuse tickets that lack an
`## Acceptance Criteria` block.

See the plugin README for the full Consumer Setup walkthrough.
```

## Errors

If any read or write fails, print which file failed and the error, then
exit. Partial scaffolding is acceptable — the consumer can re-run with
`--force` once they fix the underlying problem.

Never modify Linear, never invoke a subagent, never run shell commands
beyond what's needed to create directories and copy files.
