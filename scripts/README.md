# Cadence init-time scripts

The two helpers in this directory are invoked **only by
[`commands/init.md`](../commands/init.md)** during `/cadence:init`. They
are plugin-internal — they are never scaffolded into a consumer repo and
they never run after init.

| Script                            | Purpose                                                                                       |
|-----------------------------------|-----------------------------------------------------------------------------------------------|
| `merge_settings_hooks.py`         | Merge the Cadence hooks block from [`templates/settings.json`](../templates/settings.json) into the consumer's `.claude/settings.json`. Idempotent — re-running replaces Cadence-owned hook entries without disturbing non-Cadence ones. |
| `merge_settings_permissions.py`   | Merge the Linear MCP allowlist into the consumer's `.claude/settings.local.json` (or `--print-only` for the copy-pasteable block surfaced for `/schedule` cloud routines). |

The runtime helpers that ship to the consumer's `.claude/hooks/` live
under [`templates/hooks/`](../templates/hooks/). That directory contains
three event-hook scripts (`validate_tracking_json.py`,
`validate_workflow_on_prompt.py`, `audit_linear_writes.py`) plus four
helpers the dispatch prose invokes directly (`validate_workflow.py`,
`_common.py`, `parse_comments.py`, `emit_tracking_comment.py`). All seven
are copied verbatim by `/cadence:init` regardless of the `--force` flag —
keeping them in sync with the installed plugin is the point. The
dispatch-prose helpers are siblings of the event-hook scripts so the
copied `/cadence:*` commands can call them via
`$CLAUDE_PROJECT_DIR/.claude/hooks/...` without resolving a plugin path
at runtime (which would not exist under `/schedule`).

## Invocation contract

Both scripts use `argparse` for required args, exit `0` on success, `1`
on bad input, and emit human-readable stderr on failure. The bootstrap
prints stderr verbatim before continuing or exiting. Neither script
makes MCP or network calls — they read and write local JSON only.

## Constraints

- **Stdlib only.** No third-party dependencies (the Anthropic `/schedule`
  cloud image ships Python 3.x with the standard library; we don't take
  a `pip install` step on it). Both scripts use `json` / `argparse` /
  `re` only.
- **No MCP calls.** Writes are limited to the two settings files inside
  the consumer's `.claude/`.
