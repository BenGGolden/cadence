#!/usr/bin/env python3
"""Cadence PostToolUse hook: append a JSONL audit entry for every Linear write.

Why this exists:
  In /schedule mode there's no live terminal to watch. When something goes
  wrong, operators need to reconstruct what a fire actually did. This hook
  writes one JSON object per line to `.cadence/audit.log` for every Linear
  write tool call (comment-create, issue-update, label add/remove).

Lifetime caveat:
  In /schedule mode the audit log lives only for one fire (the routine's clone
  is discarded). In /loop mode it accumulates across fires. Durable, cross-fire
  audit history is out of scope for this plan — that lives in Linear comments.

Behaviour:
  - Scope guard: no-op (exit 0) if `.claude/workflow.yaml` is absent.
  - mkdir `.cadence/` if absent and write `.cadence/.gitignore` containing `*`
    so consumers never accidentally commit their audit log.
  - Append one JSON line: `ts`, `tool`, `issue_id`, `success`, `summary`.
  - Audit failures never block the underlying write — exit 0 unconditionally.

Stdin payload (PostToolUse):
  {"tool_name": "...", "tool_input": {...}, "tool_response": {...}, ...}

Matcher contract (kept in sync with templates/settings.example.json):
  The settings.json matcher is a regex that catches any Linear MCP write
  tool (`create_comment`, `save_comment`, `save_issue`, `update_issue`,
  `add_label`, `remove_label`) regardless of MCP server namespace prefix,
  as long as the prefix contains "linear" or "Linear". Bare tool names
  (no `mcp__<server>__` prefix) are also matched.

  Confirmed in the wild: `mcp__linear-server__*` (Linear's documented
  `claude mcp add` name). Speculative-but-cheap-to-cover: `mcp__linear__*`,
  `mcp__claude_ai_Linear__*`, bare names — see the matching rationale in
  validate_tracking_json.py. An MCP server with an unusual namespace must
  be added to the matcher manually.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

AUDIT_DIR = Path(".cadence")
AUDIT_LOG = AUDIT_DIR / "audit.log"
GITIGNORE = AUDIT_DIR / ".gitignore"


def main():
    try:
        if not Path(".claude/workflow.yaml").is_file():
            sys.exit(0)

        try:
            payload = json.loads(sys.stdin.read() or "{}")
        except json.JSONDecodeError:
            sys.exit(0)

        tool_name = payload.get("tool_name") or ""
        tool_input = payload.get("tool_input") or {}
        tool_response = (
            payload.get("tool_response")
            or payload.get("tool_result")
            or {}
        )

        line = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tool": tool_name,
            "issue_id": _extract_issue_id(tool_input),
            "success": _extract_success(tool_response),
            "summary": _summarize(tool_name, tool_input),
        }

        AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        if not GITIGNORE.is_file():
            GITIGNORE.write_text("*\n", encoding="utf-8")

        with AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception:
        # Audit failures must never block a Linear write.
        pass
    sys.exit(0)


def _extract_issue_id(tool_input):
    for key in ("issueId", "issue_id", "id"):
        v = tool_input.get(key)
        if isinstance(v, str) and v:
            return v
    return None


def _extract_success(tool_response):
    if isinstance(tool_response, dict):
        for key in ("success", "ok"):
            if key in tool_response and isinstance(tool_response[key], bool):
                return tool_response[key]
        if "error" in tool_response and tool_response["error"]:
            return False
    return True


def _summarize(tool_name, tool_input):
    body = tool_input.get("body")
    if isinstance(body, str) and body:
        return body[:80].replace("\n", " ")
    state_name = tool_input.get("stateName") or tool_input.get("state")
    if isinstance(state_name, str) and state_name:
        return f"state -> {state_name}"
    label = tool_input.get("labelId") or tool_input.get("name")
    if isinstance(label, str) and label and "label" in (tool_name or "").lower():
        return f"label: {label}"
    return ""


if __name__ == "__main__":
    main()
