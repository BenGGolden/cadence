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
error and exit. When the operator passed `--force`, forward `--force` to
the scaffold driver in step 2; otherwise omit it.

## Step 1 — Confirm working directory

You are running from the consumer's repository root. The plugin templates
live under `${CLAUDE_PLUGIN_ROOT}/templates/` and the plugin's own dispatch
prose (`commands/tick.md`, `commands/sweep.md`, `commands/status.md`) plus
helper scripts under `${CLAUDE_PLUGIN_ROOT}/scripts/`. The scaffold driver
copies the templates and the three slash commands into the consumer repo so
that `/cadence:tick` is reachable from a `/schedule` cloud routine (which
never sees the local plugin install). Do NOT write anywhere outside the
current working directory.

## Step 2 — Scaffold files

Invoke the scaffold driver via Bash. It owns the overwrite check, directory
creation, and the full source→destination copy plan (the canonical file list
lives in `scripts/scaffold_files.py:SCAFFOLD_PLAN`):

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/scaffold_files.py" \
  --plugin-root "${CLAUDE_PLUGIN_ROOT}" \
  [--force]
```

Pass `--force` only when the operator passed it to `/cadence:init`.

- **Exit 0** → print stdout (the scaffold summary) and proceed to step 3.
- **Exit 2** → print stdout (the "already initialized" abort message) and
  stop. Do not run step 3 or 4.
- **Exit 1** → print stderr (the file error) and stop. Partial scaffolding
  is acceptable — the operator can fix the underlying problem and re-run
  with `--force`.

The driver copies plugin-owned files (hook scripts under `.claude/hooks/`,
the three `/cadence:*` commands under `.claude/commands/cadence/`) on every
run, with or without `--force` — they are plugin executables kept in sync
with the installed plugin. User-config files (`workflow.yaml`,
`prompts/global.md`, `ticket-template.md`, the three `agents/*.md`) are
overwritten only with `--force`; otherwise an existing one is preserved and
named in the summary.

## Step 3 — Merge hook entries into .claude/settings.json

After the scaffold succeeds, run this command via Bash to merge Cadence's
hook entries into the consumer's `.claude/settings.json` (creating it if
absent):

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/merge_settings_hooks.py" \
  --settings-path .claude/settings.json \
  --template-path "${CLAUDE_PLUGIN_ROOT}/templates/settings.json"
```

The merge is idempotent: re-running on a settings file that already contains
Cadence's hook entries replaces them rather than duplicating, and any
non-Cadence hook entries the consumer added are left untouched.

If the script exits non-zero, print its stderr and stop — Cadence's hooks
never fire without this merge, so a broken `.claude/settings.json` means
Cadence is broken. The consumer can fix the underlying problem (usually a
hand-broken settings file) and re-run with `--force`. This is the one
stop-on-failure step after the scaffold; step 4 below is best-effort.

## Step 4 — Configure Linear MCP permissions and print next steps

Cadence's bootstrap calls a small fixed set of Linear MCP verbs every
fire. In Mode A (`/schedule`) every one of those must be pre-allowed on
the routine — a cloud routine has no human to answer permission prompts
and hangs on the first ask. In Mode B (`/loop`) pre-allowing the same set
keeps long unattended stretches from stalling on prompts. The permission
list is **operator-specific** (different installs, different MCP
namespaces), so it belongs in the untracked per-operator
`.claude/settings.local.json` — *not* in the tracked `.claude/settings.json`
where step 3's hooks block lives.

Run this single pipe via Bash. `claude mcp list` provides the installed-MCP
inventory on stdin; `configure_linear.py` detects the Linear namespace,
merges the allowlist into `.claude/settings.local.json` (falling back to a
`.mcp.json` scan, and taking a placeholder path if no Linear server is
found), and renders the operator "Next steps" handoff:

```
claude mcp list 2>/dev/null \
  | python "${CLAUDE_PLUGIN_ROOT}/scripts/configure_linear.py" \
      --plugin-root "${CLAUDE_PLUGIN_ROOT}" \
      --settings-local-path .claude/settings.local.json \
      --mcp-json-path .mcp.json
```

Its stdout **is** the operator-facing "Next steps" block — print it
verbatim. The orchestrator owns the namespace detection, the
`.claude/settings.local.json` write (or the no-write placeholder path on
detection failure), and the file list / gate-label hint / permissions block
/ next-step checklist; none of those values thread through this dispatch.

This step is best-effort: if the pipe exits non-zero, print its stderr and
finish anyway — partial scaffolding is acceptable, and the operator can
re-run with `--force`.

## Errors

If any script reports a file read or write failure, print which file failed
and the error, then stop. Partial scaffolding is acceptable — the consumer
can re-run with `--force` once they fix the underlying problem.

Never modify Linear, never invoke a subagent, never run shell commands
beyond the scaffold driver, the two settings-merge scripts, and the
`claude mcp list` inventory feeding step 4.
