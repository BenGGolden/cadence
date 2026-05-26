# Cadence helper scripts

Deterministic Python helpers the slash-command prose invokes via Bash. They
exist so mechanical bookkeeping (config validation, comment counting,
tracking-JSON emission) does not happen in LLM prose — a script always
produces the same output for the same input.

The bootstrap remains the orchestrator (only the harness can call MCP
tools); scripts handle the parts that don't need a model.

## Files

| Script                            | Purpose                                                                                       |
|-----------------------------------|-----------------------------------------------------------------------------------------------|
| `validate_workflow.py`            | Enforces the `.claude/workflow.yaml` validation rules; `--evidence` emits per-rule evidence for the dry-run report. |
| `parse_comments.py`               | Reads a Linear issue's comment list, returns attempt count, rework count, rework context, latest tracking comment, and the latest implementer summary. |
| `emit_tracking_comment.py`        | Builds canonical `<!-- cadence:state \| gate \| reconcile ... -->` comment bodies so the embedded JSON is always well-formed. |
| `merge_settings_hooks.py`         | Used by `/cadence:init` to merge the Cadence hooks block into the consumer's `.claude/settings.json`. Plugin-only — not scaffolded to the consumer. |
| `merge_settings_permissions.py`   | Used by `/cadence:init` to merge the Linear MCP allowlist into the consumer's `.claude/settings.local.json`. Plugin-only. |
| `_common.py`                      | Shared helpers (workflow.yaml loader, `die`). Imported by every script in this directory. |

`validate_workflow.py`, `parse_comments.py`, `emit_tracking_comment.py`,
and `_common.py` are also copied into the consumer's `.claude/hooks/`
directory by `/cadence:init`, so the dispatch prose can invoke them via
`"${CLAUDE_PROJECT_DIR:-.}"/.claude/hooks/<script>.py` in both `/loop` and
`/schedule` mode.

## Invocation contract

All scripts are stdin/stdout/stderr pure:

- **Args** — `argparse`. Positional for required, `--flag` for optional.
- **Large inputs** — when an input may exceed CLI arg limits (e.g. a full
  Linear comment list), the caller writes a temp file and passes
  `--input PATH`.
- **Stdout (success)** — JSON, unless the script's job is to produce a
  Linear comment body (then stdout is the comment body verbatim).
- **Stderr (failure)** — human-readable text. The bootstrap prints it
  verbatim before exiting the fire.
- **Exit codes** — `0` success, `1` bad input (e.g. unreadable YAML),
  `2` validation failure, `3` internal error.

The bootstrap prose maps these to user-visible messages; see
[`commands/tick.md`](../commands/tick.md) for the canonical wiring.

## Constraints

- **Stdlib only.** No third-party dependencies. Anthropic's `/schedule`
  cloud image ships Python 3.x with the standard library; we don't take
  a `pip install` step on it.
- **No MCP calls.** Anything that needs to read or write Linear stays in
  the prose layer (only the harness can call MCP tools).
- **Hooks must remain self-contained.** Scripts copied into the
  consumer's `.claude/hooks/` use sibling imports only — no relative
  imports back into the plugin tree.

## Hook scope guard

Each script copied into `.claude/hooks/` includes a lightweight scope
guard at the top:

```python
if not Path.cwd().joinpath(".claude/workflow.yaml").is_file():
    sys.exit(0)
```

This handles the edge case where a consumer removes their
`workflow.yaml` (decommissioning Cadence) but forgets to remove the hook
entries from `.claude/settings.json`. Cheap and obvious — keep it.

## Why scripts at all

LLM prose is non-deterministic — two `/cadence:tick` fires reading the
same Linear state can produce subtly different output if the model
re-interprets ambiguous prose. The mechanical bookkeeping (counting
attempt markers, parsing tracking JSON, validating workflow config) had
two failure modes the prose-only path was exposed to:

1. **Counting errors** under context pressure — "count prior attempt
   markers" mis-counted as the comment list grew.
2. **JSON emission / parsing errors** — a single malformed tracking
   comment poisoned attempt counting on every subsequent fire.

Moving these out of prose closes both, and the hooks layer
([`templates/hooks/`](../templates/hooks/)) backstops the case where
the LLM bypasses a script and hand-writes a Linear comment — the
`PreToolUse` validator rejects malformed tracking JSON before it
reaches Linear.
