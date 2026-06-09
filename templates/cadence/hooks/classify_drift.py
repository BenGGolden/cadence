#!/usr/bin/env python3
"""Decide whether a Cadence fire is picking up an issue that drifted.

Caller(s):
  - templates/cadence/hooks/route_fire.py (imported; the fire's drift
    sub-decision)

Pure function — no I/O, no MCP, no shell-out. Applies an ordered branch,
first match wins:

  1. `latest_tracking_comment.state` is null  → no drift (brand-new issue, or
     the latest tracking comment is a reconcile, which carries no `state`).
  2. **Match**: latest state == matched state → no drift (the previous fire
     didn't advance — its subagent failed, or a gate is still sitting in its
     waiting column awaiting a verdict).
  3. **Forward progression**: matched state == config.states[latest].next →
     no drift (the prior fire advanced Linear into latest's successor and
     this fire is the first pickup of it; step 11 emits no fresh tracking
     comment for agent→agent, so latest legitimately lags one state). Only
     applies when latest names an agent state with a defined `next`.
  4. **Drift otherwise**: a human (or other tool) reassigned the issue to a
     column not reachable from where it last was via one workflow edge.
     Returns the reconcile-comment args.

CLI (for ad-hoc inspection / tests):
  python classify_drift.py --workflow-config PATH --matched-state NAME \\
    --current-column "COLUMN" --latest-state NAME

  --latest-state may be omitted (treated as null / no prior tracking state).

Exit code: 0 always (the decision is on stdout as JSON).
"""

import argparse
import json
import sys


def classify_drift(latest_state, matched_state, current_column, states):
    """Return {"drift": bool, "reconcile_args": {...} | None}.

    `latest_state` is the latest tracking comment's `state` (or None).
    `matched_state` is the workflow state the current Linear column maps to.
    `current_column` is the issue's present Linear column (the reconcile
    body's `observed_linear_state`). `states` is the validator config's
    `states` block (for the forward-progression `next` lookup).
    """
    # 1. No prior tracking state.
    if latest_state is None:
        return {"drift": False, "reconcile_args": None}

    # 2. Match.
    if latest_state == matched_state:
        return {"drift": False, "reconcile_args": None}

    # 3. Normal forward progression (agent state with a defined `next`).
    latest_body = states.get(latest_state) if isinstance(states, dict) else None
    if isinstance(latest_body, dict):
        nxt = latest_body.get("next")
        if isinstance(nxt, str) and nxt == matched_state:
            return {"drift": False, "reconcile_args": None}

    # 4. Drift.
    return {
        "drift": True,
        "reconcile_args": {
            "observed_linear_state": current_column,
            "expected_state": latest_state,
            "reason": "human reassigned",
        },
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workflow-config", required=True,
                    help="Path to the validator's JSON output.")
    ap.add_argument("--matched-state", required=True,
                    help="Workflow state the current Linear column maps to.")
    ap.add_argument("--current-column", required=True,
                    help="The issue's present Linear column name.")
    ap.add_argument("--latest-state", default=None,
                    help="Latest tracking comment's state (omit for null).")
    args = ap.parse_args()

    try:
        with open(args.workflow_config, "r", encoding="utf-8") as fh:
            config = json.load(fh)
    except (OSError, ValueError) as e:
        print(f"Cadence: could not read --workflow-config: {e}", file=sys.stderr)
        sys.exit(1)

    states = config.get("states") or {}
    result = classify_drift(args.latest_state, args.matched_state,
                            args.current_column, states)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
