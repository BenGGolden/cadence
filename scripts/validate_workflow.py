#!/usr/bin/env python3
"""Validate .claude/workflow.yaml against Cadence's five config rules.

Caller(s):
  - commands/tick.md step 3 (live validation) — a non-zero exit blocks the
    fire before any Linear write happens.
  - commands/tick.md step 0 (dry-run) — invoked with --evidence so the
    dry-run report can show per-rule work without the LLM composing it.
  - commands/sweep.md step 1 and commands/status.md step 1 — config sanity
    before either command touches Linear.

Failure mode eliminated:
  "Validation skim" — the five rules in tick.md step 3 were LLM prose an
  agent could gloss as "passed" without showing its work. This script makes
  the checks deterministic and emits structured per-rule evidence.

CLI:
  python validate_workflow.py [--workflow-path PATH] [--evidence]

Exit codes:
  0  all five rules pass
  2  one or more rules fail
  1  the YAML could not be read or parsed at all
"""

import argparse
import json
import sys
from pathlib import Path

from _common import load_workflow

LINEAR_STATE_FIELDS = ("linear_state", "approved_linear_state", "rework_linear_state")


def _collect_linear_states(states):
    """Yield (value, "states.<name>.<field>") for every linear-column field."""
    for name, body in states.items():
        if not isinstance(body, dict):
            continue
        for field in LINEAR_STATE_FIELDS:
            val = body.get(field)
            if val is not None:
                yield str(val), f"states.{name}.{field}"


def _rule1_uniqueness(states):
    collected = list(_collect_linear_states(states))
    lines = [f"`{val}` <- {path}" for val, path in collected]
    seen = {}
    failures = []
    for val, path in collected:
        if val in seen:
            failures.append(f"{seen[val]} and {path} both = \"{val}\"")
        else:
            seen[val] = path
    ev = {
        "rule": 1,
        "title": "Linear-state uniqueness",
        "lines": lines,
        "result": "PASS" if not failures else "FAIL",
        "failure": None if not failures else "; ".join(failures),
    }
    return ev


def _rule2_entry(entry, states):
    body = states.get(entry) if isinstance(entry, str) else None
    if not isinstance(entry, str) or not entry:
        result, failure = "FAIL", "`entry` is missing or not a string."
        line = "entry -> (missing)"
    elif body is None:
        result, failure = "FAIL", f"`entry` names `{entry}`, which is not defined in states:."
        line = f"entry -> `{entry}` -> MISSING"
    else:
        etype = body.get("type")
        if etype == "agent":
            result, failure = "PASS", None
        else:
            result = "FAIL"
            failure = f"`entry` state `{entry}` has type `{etype}`, must be `agent`."
        line = f"entry -> `{entry}` -> exists, type: {etype}"
    return {
        "rule": 2,
        "title": "Entry",
        "lines": [line],
        "result": result,
        "failure": failure,
    }


def _rule3_targets(states):
    lines = []
    failures = []
    for name, body in states.items():
        if not isinstance(body, dict):
            continue
        stype = body.get("type")
        if stype == "agent":
            refs = [("next", body.get("next"))]
        elif stype == "gate":
            refs = [("on_approve", body.get("on_approve")),
                    ("on_rework", body.get("on_rework"))]
        else:
            refs = []
        for field, target in refs:
            exists = isinstance(target, str) and target in states
            lines.append(
                f"states.{name}.{field} -> `{target}` -> "
                f"{'exists' if exists else 'MISSING'}"
            )
            if not exists:
                failures.append(f"states.{name}.{field} -> `{target}` does not resolve")
    return {
        "rule": 3,
        "title": "Targets",
        "lines": lines,
        "result": "PASS" if not failures else "FAIL",
        "failure": None if not failures else "; ".join(failures),
    }


def _rule4_subagent_files(states):
    lines = []
    failures = []
    for name, body in states.items():
        if not isinstance(body, dict) or body.get("type") != "agent":
            continue
        subagent = body.get("subagent")
        if not isinstance(subagent, str) or not subagent:
            lines.append(f"states.{name}.subagent -> (missing)")
            failures.append(f"states.{name}.subagent is missing or not a string")
            continue
        agent_path = Path(".claude/agents") / f"{subagent}.md"
        exists = agent_path.is_file()
        lines.append(
            f"states.{name}.subagent -> `{agent_path.as_posix()}` -> "
            f"{'exists' if exists else 'MISSING'}"
        )
        if not exists:
            failures.append(
                f"states.{name}.subagent `{subagent}` -> "
                f"`{agent_path.as_posix()}` not found on disk"
            )
    return {
        "rule": 4,
        "title": "Subagent files",
        "lines": lines,
        "result": "PASS" if not failures else "FAIL",
        "failure": None if not failures else "; ".join(failures),
    }


def _rule5_pickup_state(linear):
    pickup = linear.get("pickup_state") if isinstance(linear, dict) else None
    ok = isinstance(pickup, str) and pickup.strip() != ""
    return {
        "rule": 5,
        "title": "Pickup state",
        "lines": [f"linear.pickup_state -> `{pickup}`"],
        "result": "PASS" if ok else "FAIL",
        "failure": None if ok else "`linear.pickup_state` must be a non-empty string.",
    }


def _build_linear_states_set(states, pickup):
    """Ordered set: pickup, then each state's linear_state, then each gate's
    approved/rework columns. Mirrors tick.md step 4."""
    ordered = []
    seen = set()

    def add(v):
        if isinstance(v, str) and v and v not in seen:
            seen.add(v)
            ordered.append(v)

    add(pickup)
    for body in states.values():
        if isinstance(body, dict):
            add(body.get("linear_state"))
    for body in states.values():
        if isinstance(body, dict) and body.get("type") == "gate":
            add(body.get("approved_linear_state"))
            add(body.get("rework_linear_state"))
    return ordered


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workflow-path", default=None,
                    help="Path to workflow.yaml (default: .claude/workflow.yaml)")
    ap.add_argument("--evidence", action="store_true",
                    help="Also emit structured per-rule evidence for the dry-run report.")
    args = ap.parse_args()

    wf = load_workflow(args.workflow_path)  # exits 1 on unreadable/unparseable YAML

    states = wf.get("states") or {}
    linear = wf.get("linear") or {}
    entry = wf.get("entry")

    if not isinstance(states, dict):
        print("Cadence: `states:` is missing or not a mapping.", file=sys.stderr)
        sys.exit(2)

    evidence = [
        _rule1_uniqueness(states),
        _rule2_entry(entry, states),
        _rule3_targets(states),
        _rule4_subagent_files(states),
        _rule5_pickup_state(linear),
    ]

    failures = [ev for ev in evidence if ev["result"] == "FAIL"]
    valid = not failures

    pickup = linear.get("pickup_state") if isinstance(linear, dict) else None
    entry_body = states.get(entry) if isinstance(entry, str) else None
    result = {
        "valid": valid,
        "entry_state_name": entry if isinstance(entry, str) else None,
        "entry_subagent": (entry_body or {}).get("subagent") if isinstance(entry_body, dict) else None,
        "workflow_linear_states": _build_linear_states_set(states, pickup),
        "pickup_state": pickup,
        "states": states,
    }

    if args.evidence:
        result["evidence"] = evidence
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not valid:
            for ev in failures:
                print(f"Rule {ev['rule']} ({ev['title']}) FAILED:\n  {ev['failure']}",
                      file=sys.stderr)
            sys.exit(2)
        sys.exit(0)

    if not valid:
        for ev in failures:
            print(f"Rule {ev['rule']} ({ev['title']}) FAILED:\n  {ev['failure']}",
                  file=sys.stderr)
        sys.exit(2)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
