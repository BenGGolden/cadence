#!/usr/bin/env python3
"""Decide what a Cadence merge-on-approve sub-phase does with an issue's PR.

Caller(s):
  - commands/tick.md Step 6 Execute (the Merge-on-approve sub-phase, opt-in via
    a gate's `merge_on_approve: true`). The bootstrap runs `gh pr view` first,
    writes its JSON to a file, and asks this helper what to do next.

Pure function — no I/O, no MCP, no shell-out. It reads the `state` field of a
`gh pr view --json state` payload and returns the routing decision:

  - MERGED                     → **advance**: the PR is already merged (e.g. a
                                 human merged it manually — the idempotency
                                 case). The bootstrap advances to the terminal
                                 without re-merging.
  - OPEN                       → **merge**: attempt `gh pr merge`.
  - CLOSED                     → **escalate**: closed but unmerged (abandoned
                                 PR); do not silently land the card in Done.
  - missing / unrecognized /   → **escalate**: the bootstrap could not
    non-dict                     determine PR state; escalate to a human.

CLI (for ad-hoc inspection / tests):
  python classify_merge.py --input PATH

  --input is a JSON file holding the `gh pr view --json state,url` output (or
  any dict carrying a `state` field).

Exit code: 0 always (the decision is on stdout as JSON). On an unreadable
--input file, the `escalate` outcome is printed on stdout (still exit 0) — the
bootstrap needs a verdict either way.
"""

import argparse
import io
import json
import sys

# The plan can carry a non-ASCII reason; force UTF-8 so stdout is stable
# regardless of the parent locale (Windows defaults to cp1252).
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  newline="")


def classify_merge(pr_state):
    """Return the merge routing plan.

    Shape:
      {
        "action": "advance" | "merge" | "escalate",
        "reason": "<short>",
      }
    """
    if not isinstance(pr_state, dict):
        return {
            "action": "escalate",
            "reason": "could not determine PR state from `gh pr view` output",
        }

    state = pr_state.get("state")
    if state == "MERGED":
        return {"action": "advance", "reason": "PR already merged"}
    if state == "OPEN":
        return {"action": "merge", "reason": "PR is open; attempting merge"}
    if state == "CLOSED":
        return {"action": "escalate", "reason": "PR is closed but not merged"}
    return {
        "action": "escalate",
        "reason": "could not determine PR state from `gh pr view` output",
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True,
                    help="Path to a JSON file with the `gh pr view` output.")
    args = ap.parse_args()

    try:
        with open(args.input, "r", encoding="utf-8") as fh:
            pr_state = json.load(fh)
    except (OSError, ValueError):
        # The bootstrap needs a verdict either way; an unreadable PR-state file
        # is itself an escalation (same philosophy as
        # promote_acceptance_criteria.py — never block on a read error).
        pr_state = None

    result = classify_merge(pr_state)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
