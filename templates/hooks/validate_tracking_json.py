#!/usr/bin/env python3
"""Cadence PreToolUse hook: validate tracking-comment JSON before Linear write.

Fires on Linear comment-create tool calls. If the comment body is a Cadence
tracking comment (starts with `<!-- cadence:` or `<!-- stokowski:`), the embedded
JSON object is extracted and parsed. If parsing fails, the tool call is blocked
with a clear diagnostic.

Why this exists:
  Tracking-comment bodies embed JSON that downstream fires read back to count
  attempts and route rework. An LLM that hand-writes a comment instead of going
  through `scripts/emit_tracking_comment.py` can produce malformed JSON, which
  poisons every subsequent fire's bookkeeping. This hook is the last line of
  defence before bad JSON reaches Linear.

Behaviour:
  - Scope guard: no-op (exit 0) if `.claude/workflow.yaml` is absent.
  - Not a Cadence comment: no-op (exit 0).
  - Cadence comment with parseable JSON: allow (exit 0).
  - Cadence comment with unparseable JSON: block (exit 2) with diagnostic.

Stdin payload (PreToolUse):
  {"tool_name": "...", "tool_input": {"body": "..."}, "tool_use_id": "..."}

Matcher contract (kept in sync with templates/settings.example.json):
  The settings.json matcher is a regex that catches any Linear MCP tool
  named `create_comment` or `save_comment` regardless of the MCP server's
  namespace prefix, as long as the prefix contains "linear" or "Linear".
  Bare tool names (no `mcp__<server>__` prefix) are also matched.

  Known names this catches in the wild:
    mcp__linear__create_comment              (linear-official MCP)
    mcp__linear-server__save_comment         (locally-named `linear-server`)
    mcp__claude_ai_Linear__save_comment      (claude.ai connector)
    save_comment / create_comment            (bare)

  An MCP server whose namespace does NOT contain "linear" (case-insensitive
  for the leading character) will not match — operators with an unusual
  namespace must extend the matcher manually in .claude/settings.json.
"""

import json
import re
import sys
from pathlib import Path

CADENCE_PREFIX_RE = re.compile(r"^\s*<!--\s*(cadence|stokowski):", re.IGNORECASE)


def main():
    if not Path(".claude/workflow.yaml").is_file():
        sys.exit(0)

    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        # Malformed hook payload is not our problem — fail-open so we never
        # block a tool call we don't understand.
        sys.exit(0)

    tool_input = payload.get("tool_input") or {}
    body = tool_input.get("body")
    if not isinstance(body, str):
        sys.exit(0)

    if not CADENCE_PREFIX_RE.match(body):
        sys.exit(0)

    kind_match = re.match(r"^\s*<!--\s*((?:cadence|stokowski):\S+)", body, re.IGNORECASE)
    kind = kind_match.group(1) if kind_match else "<unknown>"

    start = body.find("{")
    if start < 0:
        _block(kind, "no JSON object found in comment body", body)

    depth = 0
    end = -1
    in_str = False
    esc = False
    for i in range(start, len(body)):
        ch = body[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break

    if end < 0:
        _block(kind, "unbalanced braces in JSON block", body)

    snippet = body[start : end + 1]
    try:
        json.loads(snippet)
    except json.JSONDecodeError as e:
        _block(kind, f"json.loads: {e}", body)

    sys.exit(0)


def _block(kind, reason, body):
    truncated = body[:200] + ("..." if len(body) > 200 else "")
    print(
        "Cadence hook: tracking comment JSON failed validation.\n"
        f"Comment kind: {kind}\n"
        f"Parser error: {reason}\n"
        f"First 200 chars of body: {truncated}",
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
