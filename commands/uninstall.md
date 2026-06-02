---
description: Remove Cadence from this repo — deletes scaffolded .claude/ files, unmerges hook + permission entries, and prints the Linear-side cleanup checklist.
argument-hint: "[--dry-run] [--force]"
disable-model-invocation: true
---

# /cadence:uninstall

You are the Cadence decommissioner. **Run exactly once and exit.** Your job is
to reverse what `/cadence:init` scaffolded into this repo: the plugin-owned
files under `.claude/`, Cadence's hook entries in `.claude/settings.json`, the
Linear-MCP allowlist in `.claude/settings.local.json`, and the `.cadence/`
runtime scratch dir — then print the Linear-side cleanup the plugin cannot
safely do for you.

You do **NOT** call any Linear MCP tool, you do **NOT** invoke any subagent,
and you run **no shell beyond the four scripts named below**.

## Step 0 — Capture arguments

The argument string may contain any combination of `--dry-run` and `--force`
(case-insensitive), in any order, or be empty. Capture which were passed.
Treat any other token as invalid input — print an error naming the bad token
and exit without touching anything. Forward the captured flags to the scripts
in the steps that accept them.

- `--dry-run` → preview only; every script writes/deletes nothing.
- `--force` → also remove user-config files (`workflow.yaml`,
  `prompts/global.md`, `agents/*.md`, `ticket-template.md`), which you may
  have edited and committed.

## Step 1 — Confirm working directory & intent

You are running from the consumer's repository root (the same place
`/cadence:init` ran). Do NOT write anywhere outside it.

Without `--dry-run`, this **permanently deletes files**; git history is the
only recovery path. Per the operator's standing preference to confirm before
destructive operations, on a real run that was **not** passed `--force`,
recommend running `/cadence:uninstall --dry-run` first and confirm the
operator wants to proceed before continuing. (Uninstall is a deliberate
local-only command — an interactive confirm is acceptable here; it is never
reached under `/schedule`.) On a `--dry-run` invocation, proceed without
confirming.

## Step 2 — Remove files

Invoke the removal driver via Bash. It owns the per-file removal policy,
`.cadence/` cleanup, and empty-dir pruning (the canonical file list is
`scripts/scaffold_files.py:SCAFFOLD_PLAN`, which the driver imports):

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/unscaffold_files.py" [--dry-run] [--force]
```

Print stdout (the removal summary). Exit 1 means one or more deletions failed —
print stderr and continue to step 3 (a single stuck file should not abort the
rest of the uninstall).

## Step 3 — Unmerge hook entries from .claude/settings.json

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/merge_settings_hooks.py" --remove --settings-path .claude/settings.json [--dry-run]
```

Print stdout. The script strips only Cadence hook entries (non-Cadence hooks
survive) and deletes the file only if it reduces to `{}`. On exit 1 (an
unparseable settings file it refuses to corrupt), print stderr and continue to
step 4 — don't abort the whole uninstall over one file.

## Step 4 — Unmerge the Linear-MCP allowlist from .claude/settings.local.json

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/merge_settings_permissions.py" --remove --settings-path .claude/settings.local.json [--dry-run]
```

Print stdout. Same best-effort handling as step 3 (print stderr on exit 1 and
continue). No `--namespace` is needed — removal matches any Cadence-owned
`linear`-namespaced entry.

## Step 5 — Print the Linear-cleanup checklist

Always run this, even on `--dry-run` (it writes nothing):

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/render_uninstall_steps.py"
```

Print its stdout verbatim. It is the operator's hand-cleanup list for the
Linear side Cadence never touches — the four `cadence-*` labels and the
workflow columns mapped in `workflow.yaml`.

## Step 6 — Exit

Exit cleanly. Do **NOT** call Linear MCP. Do **NOT** invoke any subagent. Do
**NOT** run any shell beyond the four scripts above. Do **NOT** loop.
