# Cadence init-time scripts

The six helpers in this directory are invoked **only by
[`commands/init.md`](../commands/init.md)** during `/cadence:init`. They
are plugin-internal — they are never scaffolded into a consumer repo and
they never run after init.

| Script                              | Purpose                                                                                       |
|-------------------------------------|-----------------------------------------------------------------------------------------------|
| `scaffold_files.py`                 | Step 2 driver. Owns the overwrite check, directory creation, and the canonical source→destination copy plan (`SCAFFOLD_PLAN`). Copies plugin-owned files (hooks, `/cadence:*` commands) unconditionally and user-config files only with `--force`. Exit 2 = already initialized (abort message on stdout). Single source of truth for the file list — `render_next_steps.py` imports `SCAFFOLD_PLAN`. |
| `merge_settings_hooks.py`           | Step 3. Merge the Cadence hooks block from [`templates/settings.json`](../templates/settings.json) into the consumer's `.claude/settings.json`. Idempotent — re-running replaces Cadence-owned hook entries without disturbing non-Cadence ones. Stop-on-failure (hooks never fire without it). |
| `configure_linear.py`               | Step 4 orchestrator. Reads `claude mcp list` on stdin, detects the Linear MCP namespace, merges the allowlist into `.claude/settings.local.json` (placeholder path on detection failure), and renders the "Next steps" block on stdout. Thin layer over the three helpers below — no detection/merge/render logic of its own. Best-effort. |
| `merge_settings_permissions.py`     | Merge the Linear MCP allowlist into the consumer's `.claude/settings.local.json` (or `--print-only` for the copy-pasteable block surfaced for `/schedule` cloud routines). Imported by `configure_linear.py`. |
| `detect_linear_mcp_namespace.py`    | Scan `claude mcp list` stdout (via stdin) and/or `.mcp.json` to detect the consumer's Linear MCP server namespace. Exit 2 = no Linear server found. Imported by `configure_linear.py`. |
| `render_next_steps.py`              | Render the "Cadence initialised." operator handoff block — file list (from `SCAFFOLD_PLAN`), gate-label hint, permissions block, next-step checklist — with three interpolation points for the settings.local outcome, detection note, and permissions block. `render()` is called by `configure_linear.py`. |

The runtime helpers that ship to the consumer's `.claude/hooks/` live
under [`templates/hooks/`](../templates/hooks/) — three event-hook scripts
plus the dispatch-prose helpers the `/cadence:*` commands invoke directly.
They are copied verbatim by `scaffold_files.py` regardless of the `--force`
flag (they are tagged `plugin-owned` in `SCAFFOLD_PLAN`) — keeping them in
sync with the installed plugin is the point. The dispatch-prose helpers are
siblings of the event-hook scripts so the copied `/cadence:*` commands can
call them via `$CLAUDE_PROJECT_DIR/.claude/hooks/...` without resolving a
plugin path at runtime (which would not exist under `/schedule`). The full,
canonical list of what gets copied lives in
[`scaffold_files.py`](./scaffold_files.py)'s `SCAFFOLD_PLAN`.

## Invocation contract

These scripts use `argparse` for required args, exit `0` on success, `1`
on bad input, and emit human-readable stderr on failure (`scaffold_files.py`
and `merge_*` also use exit `2` for a stop signal that isn't an internal
error). The bootstrap prints stderr verbatim before continuing or exiting.
No script makes MCP or network calls — they read and write local files
only; `configure_linear.py` reads the `claude mcp list` inventory on stdin
but never spawns a CLI child of its own.

## Constraints

- **Stdlib only.** No third-party dependencies (the Anthropic `/schedule`
  cloud image ships Python 3.x with the standard library; we don't take
  a `pip install` step on it). The scripts use only `json` / `argparse` /
  `re` / `pathlib` / `io`.
- **No MCP calls.** Writes are limited to the scaffolded `.claude/` tree
  and the two settings files inside it.

## Tests

The runtime helpers under [`templates/hooks/`](../templates/hooks/) are
covered by a `unittest`-based suite under [`tests/`](../tests/) at the
repo root. One test file per script. Run:

```sh
python -m unittest discover -s tests -v
```

Each test materialises its inputs in a `tempfile.TemporaryDirectory()`
and invokes the script via subprocess so the full argparse + I/O path
runs. Larger fixtures (golden Markdown, JSON payloads) belong under
`tests/fixtures/`; inline literals are fine until they grow past a few
lines. CI runs the discover command in the `python-tests` job in
[`.github/workflows/validate.yml`](../.github/workflows/validate.yml).
