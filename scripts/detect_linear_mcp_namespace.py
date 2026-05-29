#!/usr/bin/env python3
"""Detect the consumer's Linear MCP server namespace for /cadence:init.

Plugin-only helper — imported by scripts/configure_linear.py (the
commands/init.md Step 4 orchestrator). Not scaffolded to the consumer;
lives in scripts/ (init-time only).

Three Linear MCP namespaces show up in the wild:
  - `linear`              — the official Linear MCP server installed under
                            the name `linear`.
  - `linear-server`       — same server, installed under `linear-server`
                            (common on Windows installs that follow Linear's
                            docs).
  - `claude_ai_Linear`    — the claude.ai workspace connector.

The orchestrator (init.md Step 4) needs the namespace string so
`scripts/merge_settings_permissions.py` can write
`mcp__<namespace>__<verb>` entries into `.claude/settings.local.json`.

Detection strategy:

  1. `--mcp-list-stdin`  — read `claude mcp list` output from stdin and
     return the first server-name token that contains "linear"
     (case-insensitive). Strips leading bullets/whitespace (`*`, `-`,
     spaces, tabs) and stops at the first whitespace, colon, or comma
     after the name. The claude.ai workspace connector is a special case:
     it lists by display name (`claude.ai Linear: ...`) rather than a bare
     token, so that form is recognised and mapped to its underscore
     namespace `claude_ai_Linear`. Extra matches are reported on stderr
     (init.md uses this to surface a "you may have picked the wrong one"
     hint).
  2. `--mcp-json-path PATH`  — fall back to reading a `.mcp.json` file
     and scanning its top-level `mcpServers` object for a key whose name
     matches the same pattern.

Either flag may be passed alone, or both together (stdin first, JSON
fallback). When both fail, stdout is empty and exit is 2 — init.md
treats this as "detection failed" and surfaces a placeholder.

CLI:
  python detect_linear_mcp_namespace.py --mcp-list-stdin
  python detect_linear_mcp_namespace.py --mcp-json-path .mcp.json
  python detect_linear_mcp_namespace.py --mcp-list-stdin \\
      --mcp-json-path .mcp.json

Exit codes:
  0  success — namespace printed on stdout, no trailing newline issues
  1  bad input (missing/unreadable --mcp-json-path file)
  2  detection failed — no Linear MCP server found in either source
"""

import argparse
import json
import re
import sys
from pathlib import Path

# Match a token containing "linear" (case-insensitive) bounded by the
# allowed namespace character set [A-Za-z0-9_-]. Anchored to the start of
# the (already-stripped) line; trailing characters are bounded by a word
# boundary or end-of-line.
_NAME_RE = re.compile(r"^([A-Za-z0-9_-]*[Ll]inear[A-Za-z0-9_-]*)")

# Strip leading bullet markers and whitespace from `claude mcp list` output.
# Handles `* name`, `- name`, `  name`, and `*   name`.
_BULLET_PREFIX_RE = re.compile(r"^[\s\*\-]+")

# The claude.ai workspace connector lists with a human *display name*, not a
# bare namespace token, e.g.:
#   claude.ai Linear: https://mcp.linear.app/mcp - ✓ Connected
# The harness exposes its tools under the underscore namespace
# `claude_ai_Linear` (see mcp__claude_ai_Linear__* tool names). The bare-token
# _NAME_RE can't match this — it stops at the `.` after "claude" before
# reaching "linear". Detect the `claude.ai <Name>` display form and map the
# text before the first colon to the underscore namespace.
_CLAUDE_AI_PREFIX_RE = re.compile(r"^claude\.ai\b", re.IGNORECASE)
_NON_NAMESPACE_RE = re.compile(r"[^A-Za-z0-9]+")


def _match_line_namespace(line):
    """Return the Linear MCP namespace named on `line`, or None.

    Tries the bare server-name token first (the official `linear` /
    `linear-server` installs and the `claude_ai_Linear` key form), then the
    claude.ai connector's `claude.ai <Name>:` display form.
    """
    m = _NAME_RE.match(line)
    if m:
        return m.group(1)
    if (_CLAUDE_AI_PREFIX_RE.match(line)
            and "linear" in line.lower() and ":" in line):
        display = line.split(":", 1)[0]
        namespace = _NON_NAMESPACE_RE.sub("_", display).strip("_")
        return namespace or None
    return None


def _scan_mcp_list(text):
    """Walk `claude mcp list` output line-by-line; return (first, extras).

    `first` is the first server-name token containing "linear" (case-
    insensitive), or None when nothing matches. `extras` is a list of
    additional matches encountered after `first` — init.md surfaces these
    so the operator can adjust if they picked the wrong one.
    """
    first = None
    extras = []
    for raw_line in text.splitlines():
        line = _BULLET_PREFIX_RE.sub("", raw_line)
        if not line:
            continue
        name = _match_line_namespace(line)
        if name is None:
            continue
        if first is None:
            first = name
        elif name != first:
            extras.append(name)
    return first, extras


def _scan_mcp_json(path):
    """Parse the JSON file at `path`; return (first, extras) over the keys
    of its top-level `mcpServers` object. Returns (None, []) when the file
    has no `mcpServers` key or it isn't an object.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"Cadence: could not parse {path}: {e}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(data, dict):
        return None, []
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        return None, []
    first = None
    extras = []
    for key in servers.keys():
        if not isinstance(key, str):
            continue
        m = _NAME_RE.match(key)
        if not m:
            continue
        name = m.group(1)
        if first is None:
            first = name
        elif name != first:
            extras.append(name)
    return first, extras


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mcp-list-stdin", action="store_true",
                    help="Read `claude mcp list` output from stdin.")
    ap.add_argument("--mcp-json-path",
                    help="Fallback: scan a .mcp.json file's mcpServers keys.")
    args = ap.parse_args()

    if not args.mcp_list_stdin and not args.mcp_json_path:
        print("Cadence: pass --mcp-list-stdin and/or --mcp-json-path.",
              file=sys.stderr)
        sys.exit(1)

    first = None
    extras = []

    if args.mcp_list_stdin:
        stdin_text = sys.stdin.read()
        first, extras = _scan_mcp_list(stdin_text)

    if first is None and args.mcp_json_path:
        json_path = Path(args.mcp_json_path)
        if not json_path.is_file():
            sys.exit(2)
        first, extras = _scan_mcp_json(json_path)

    if first is None:
        sys.exit(2)

    if extras:
        print(
            "Cadence: multiple Linear MCP servers found "
            f"(picked {first}; also saw: {', '.join(extras)}). "
            "Adjust --namespace by hand if the wrong one was chosen.",
            file=sys.stderr,
        )

    print(first)
    sys.exit(0)


if __name__ == "__main__":
    main()
