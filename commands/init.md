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
    - .claude/hooks/compose_lifecycle_context.py
    - .claude/hooks/filter_candidates.py
    - .claude/hooks/render_status_report.py
    - .claude/hooks/render_sweep_report.py
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

The `${CLAUDE_PLUGIN_ROOT}/templates/` tree mirrors the consumer's
`.claude/` tree 1:1 — every file under `templates/` is copied to the same
relative path under `.claude/`. The three slash-command files live at
`${CLAUDE_PLUGIN_ROOT}/commands/` (plugin-manifest contract) and copy to
`.claude/commands/cadence/`.

| Source                                                            | Destination                  |
|-------------------------------------------------------------------|------------------------------|
| `${CLAUDE_PLUGIN_ROOT}/templates/workflow.yaml`                   | `.claude/workflow.yaml`      |
| `${CLAUDE_PLUGIN_ROOT}/templates/prompts/global.md`               | `.claude/prompts/global.md`  |
| `${CLAUDE_PLUGIN_ROOT}/templates/ticket-template.md`              | `.claude/ticket-template.md` |
| `${CLAUDE_PLUGIN_ROOT}/templates/agents/planner.md`               | `.claude/agents/planner.md`  |
| `${CLAUDE_PLUGIN_ROOT}/templates/agents/implementer.md`           | `.claude/agents/implementer.md` |
| `${CLAUDE_PLUGIN_ROOT}/templates/agents/reviewer.md`              | `.claude/agents/reviewer.md` |
| `${CLAUDE_PLUGIN_ROOT}/templates/hooks/validate_tracking_json.py` | `.claude/hooks/validate_tracking_json.py` |
| `${CLAUDE_PLUGIN_ROOT}/templates/hooks/validate_workflow_on_prompt.py` | `.claude/hooks/validate_workflow_on_prompt.py` |
| `${CLAUDE_PLUGIN_ROOT}/templates/hooks/audit_linear_writes.py`    | `.claude/hooks/audit_linear_writes.py` |
| `${CLAUDE_PLUGIN_ROOT}/templates/hooks/validate_workflow.py`      | `.claude/hooks/validate_workflow.py` |
| `${CLAUDE_PLUGIN_ROOT}/templates/hooks/_common.py`                | `.claude/hooks/_common.py`   |
| `${CLAUDE_PLUGIN_ROOT}/templates/hooks/parse_comments.py`         | `.claude/hooks/parse_comments.py` |
| `${CLAUDE_PLUGIN_ROOT}/templates/hooks/emit_tracking_comment.py`  | `.claude/hooks/emit_tracking_comment.py` |
| `${CLAUDE_PLUGIN_ROOT}/templates/hooks/compose_lifecycle_context.py` | `.claude/hooks/compose_lifecycle_context.py` |
| `${CLAUDE_PLUGIN_ROOT}/templates/hooks/filter_candidates.py`      | `.claude/hooks/filter_candidates.py` |
| `${CLAUDE_PLUGIN_ROOT}/templates/hooks/render_status_report.py`   | `.claude/hooks/render_status_report.py` |
| `${CLAUDE_PLUGIN_ROOT}/templates/hooks/render_sweep_report.py`    | `.claude/hooks/render_sweep_report.py` |
| `${CLAUDE_PLUGIN_ROOT}/commands/tick.md`                          | `.claude/commands/cadence/tick.md` |
| `${CLAUDE_PLUGIN_ROOT}/commands/sweep.md`                         | `.claude/commands/cadence/sweep.md` |
| `${CLAUDE_PLUGIN_ROOT}/commands/status.md`                        | `.claude/commands/cadence/status.md` |

The agent templates already carry their final `name:` (`planner`,
`implementer`, `reviewer`) in their frontmatter — copy them verbatim. The
consumer's `workflow.yaml` references these short names.

The eleven files copied into `.claude/hooks/` and the three files copied
into `.claude/commands/cadence/` are always overwritten on init (including
without `--force`). They are plugin-owned executables and dispatch prose,
not user config; keeping them in sync with the installed plugin is the
point. The eight `.py` files at `templates/hooks/` that are also called from
the dispatch prose (`validate_workflow.py`, `_common.py`, `parse_comments.py`,
`emit_tracking_comment.py`, `compose_lifecycle_context.py`,
`filter_candidates.py`, `render_status_report.py`, `render_sweep_report.py`)
are siblings of the three event-hook scripts so that the `UserPromptSubmit`
hook and the copied `/cadence:*` commands can call them via
`$CLAUDE_PROJECT_DIR/.claude/hooks/...` without resolving a plugin path at
runtime — which is what makes the workflow runnable from a `/schedule` cloud
routine (where `${CLAUDE_PLUGIN_ROOT}` is not defined because the plugin
is not installed in the cloud session).

If `--force` was supplied and a destination already exists, overwrite it.
If `--force` was NOT supplied (which means step 2 fell through because
`.claude/workflow.yaml` was absent), still avoid clobbering any of the
agent or prompt destinations that happen to exist already — print a warning
naming each one you skipped, but continue with the rest. The eleven files
under `.claude/hooks/` and the three files under `.claude/commands/cadence/`
are always copied regardless (see paragraph above).

## Step 4b — Merge hook entries into .claude/settings.json

After the file copies succeed, run this command via Bash to merge Cadence's
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

If the script exits non-zero, print its stderr and stop — partial
scaffolding is acceptable (file copies have already happened), and the
consumer can fix the underlying problem (usually a hand-broken
`.claude/settings.json`) and re-run with `--force`.

## Step 4c — Merge Linear MCP permissions into .claude/settings.local.json

Cadence's bootstrap calls a small fixed set of Linear MCP verbs every
fire. In Mode A (`/schedule`) every one of those must be pre-allowed on
the routine — a cloud routine has no human to answer permission prompts
and hangs on the first ask. In Mode B (`/loop`) pre-allowing the same set
keeps long unattended stretches from stalling on prompts.

The permission list is **operator-specific** (different installs, different
MCP namespaces), so it belongs in the untracked per-operator
`.claude/settings.local.json` — *not* in `.claude/settings.json` (which is
tracked and where the hooks block from step 4b lives).

### Detect the Linear MCP namespace

Identify the consumer's Linear MCP server namespace. Three namespaces
show up in the wild:

- `linear` — the official Linear MCP server installed under the name `linear`.
- `linear-server` — same server, installed under the name `linear-server`
  (common on Windows installs that follow Linear's docs).
- `claude_ai_Linear` — the claude.ai workspace connector.

Run `claude mcp list` via Bash and pipe its stdout to
`scripts/detect_linear_mcp_namespace.py`:

```
claude mcp list 2>/dev/null \
  | python "${CLAUDE_PLUGIN_ROOT}/scripts/detect_linear_mcp_namespace.py" --mcp-list-stdin
```

- Exit 0 → stdout is the detected namespace. Capture it as
  `linearNamespace`. Any "multiple Linear MCP servers" warning the script
  writes to stderr is included for the operator and can be passed through
  verbatim.
- Exit 2 → CLI detection found nothing. Re-invoke against `.mcp.json`:

  ```
  python "${CLAUDE_PLUGIN_ROOT}/scripts/detect_linear_mcp_namespace.py" \
    --mcp-json-path .mcp.json
  ```

  Exit 0 → use the printed namespace. Exit 2 → treat detection as
  **failed** and skip to "Detection failed" below.

### Run the merge

With a detected namespace, invoke Bash:

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/merge_settings_permissions.py" \
  --settings-path .claude/settings.local.json \
  --namespace <linearNamespace>
```

The merge is idempotent: re-running replaces Cadence-owned entries
(matched by the heuristic `mcp__<namespace-containing-linear>__<canonical-verb>`)
rather than duplicating, and leaves unrelated entries in `permissions.allow`
alone. If the script exits non-zero, print its stderr and continue —
partial scaffolding is acceptable.

Also invoke it once more with `--print-only` to capture the verb list
for the "Next steps" output (see step 5):

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/merge_settings_permissions.py" \
  --print-only --namespace <linearNamespace>
```

Hold the stdout as `permissionsBlock` for step 5.

### Detection failed

If no Linear MCP server was detected, do **not** write
`.claude/settings.local.json` (you would not know which namespace to
prefix). Generate `permissionsBlock` with a placeholder by invoking:

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/merge_settings_permissions.py" \
  --print-only --namespace REPLACE_WITH_YOUR_LINEAR_MCP_NAMESPACE
```

Capture its stdout as `permissionsBlock`. Step 5 will surface it with a
one-line pointer to the README's "Linear MCP tools" section so the
operator knows what to substitute. A failed detection is not a fatal
error — the consumer can still hand-edit.

## Step 5 — Print next steps

After all writes succeed, invoke `scripts/render_next_steps.py` and
print its stdout verbatim. The renderer owns the file list, the gate-
label hint, and the next-step checklist; the dispatch supplies three
runtime values:

- `--settings-local-written` — `true` when step 4c wrote
  `.claude/settings.local.json` (the renderer includes the line under
  "Files written"); `false` when detection failed (line omitted).
- `--permissions-detection-note` — a single-line note for the operator.
  Either `Detected Linear MCP namespace: <linearNamespace>` when
  detection succeeded, or the longer
  `No Linear MCP server detected. Substitute
  <REPLACE_WITH_YOUR_LINEAR_MCP_NAMESPACE> below with your actual
  namespace (see README "Linear MCP tools" for the three variants in the
  wild), then add each line to your .claude/settings.local.json
  permissions.allow array.` when it failed.
- `--permissions-block` — the raw `permissionsBlock` stdout captured in
  step 4c (one canonical allowlist entry per line). The renderer
  indents each line two spaces under the section header.

Invoke via Bash:

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/render_next_steps.py" \
  --settings-local-written {true|false} \
  --permissions-detection-note "$detectionNote" \
  --permissions-block "$permissionsBlock"
```

Print its stdout verbatim. If the script exits non-zero, print its
stderr — partial scaffolding is acceptable, the operator can re-run
with `--force`.

## Errors

If any read or write fails, print which file failed and the error, then
exit. Partial scaffolding is acceptable — the consumer can re-run with
`--force` once they fix the underlying problem.

Never modify Linear, never invoke a subagent, never run shell commands
beyond what's needed to create directories and copy files.
