#!/usr/bin/env python3
"""Validate .claude/workflow.yaml against Cadence's config rules.

Caller(s):
  - commands/tick.md step 3 (live validation) — a non-zero exit blocks the
    fire before any Linear write happens.
  - commands/tick.md step 0 (dry-run) — invoked with --evidence so the
    dry-run report can show per-rule work without the LLM composing it.
  - commands/sweep.md step 1 and commands/status.md step 1 — config sanity
    before either command touches Linear.

Failure mode eliminated:
  "Validation skim" — the rules in tick.md step 3 were LLM prose an agent
  could gloss as "passed" without showing its work. This script makes the
  checks deterministic and emits structured per-rule evidence.

Rules implemented in this script: 1, 2, 3, 4, 5, 6, 7, 8. Rule numbers
are not ship-order — they reflect the hardening-plan phase that added
each rule.

CLI:
  python validate_workflow.py [--workflow-path PATH] [--evidence]

Exit codes:
  0  all rules pass
  2  one or more rules fail
  1  the YAML could not be read or parsed at all

JSON output (stdout) contains the derived fields the dispatch prose needs
(`entry_state_name`, `entry_subagent`, `workflow_linear_states`,
`linear_to_workflow`, `pickup_state`, `states`) **plus** the raw top-level
`linear`, `label`, and `limits` blocks from the YAML. The prose reads
team / project / label / limits values from this output instead of
re-reading `.claude/workflow.yaml` itself — one read per fire, one
cacheable artifact eliminated.
"""

import argparse
import json
import sys
from pathlib import Path

from _common import ensure_cadence_dir, load_workflow

LEGACY_GATE_KEYS = ("approved_linear_state", "rework_linear_state")


def _collect_linear_states(states, pickup):
    """Yield (value, "<path>") for every workflow Linear column.

    Covers `linear.pickup_state` plus each state's `linear_state`. The
    legacy per-gate `approved_linear_state` / `rework_linear_state` fields
    were removed in P4 (rule 8 rejects them).
    """
    if isinstance(pickup, str) and pickup:
        yield str(pickup), "linear.pickup_state"
    for name, body in states.items():
        if not isinstance(body, dict):
            continue
        val = body.get("linear_state")
        if val is not None:
            yield str(val), f"states.{name}.linear_state"


def _rule1_uniqueness(states, pickup):
    collected = list(_collect_linear_states(states, pickup))
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


def _rule6_max_in_flight(states):
    """`max_in_flight`, where present, must be a positive integer (>= 1)
    and may only appear on `type: agent` or `type: gate` states.
    Terminals are excluded (they have no pickup to throttle).

    Agent caps throttle parallel subagent runs at the state itself. Gate
    caps throttle the *waiting queue*: tick.md Step 5 walks each
    candidate's happy-path downstream and drops it if any state on that
    path (agent or gate) is over-cap. Verdict-bearing gate candidates
    are exempt from their own gate's cap because acting on a verdict
    drains the queue (P8.2)."""
    lines = []
    failures = []
    for name, body in states.items():
        if not isinstance(body, dict) or "max_in_flight" not in body:
            continue
        val = body.get("max_in_flight")
        stype = body.get("type")
        lines.append(
            f"states.{name}.max_in_flight -> {val!r} (state type: {stype})"
        )
        # bool is a subclass of int — reject it explicitly so True/False
        # don't slip through as 1/0.
        if isinstance(val, bool) or not isinstance(val, int) or val < 1:
            failures.append(
                f"states.{name}.max_in_flight must be a positive integer "
                f"(>= 1), got {val!r}"
            )
        if stype not in ("agent", "gate"):
            failures.append(
                f"states.{name}.max_in_flight is only valid on "
                f"`type: agent` or `type: gate` states; `{name}` is "
                f"`type: {stype}`"
            )
    if not lines:
        lines.append("(no states declare max_in_flight)")
    return {
        "rule": 6,
        "title": "max_in_flight type and scope",
        "lines": lines,
        "result": "PASS" if not failures else "FAIL",
        "failure": None if not failures else "; ".join(failures),
    }


def _rule7_adversarial_context(states):
    """`adversarial_context`, where present, must be a boolean and may only
    appear on `type: agent` states. The flag controls how the bootstrap
    composes the Lifecycle Context for a subagent invocation (tick.md
    Step 13); gates and terminals invoke no subagent (P5.4a)."""
    lines = []
    failures = []
    for name, body in states.items():
        if not isinstance(body, dict) or "adversarial_context" not in body:
            continue
        val = body.get("adversarial_context")
        stype = body.get("type")
        lines.append(
            f"states.{name}.adversarial_context -> {val!r} (state type: {stype})"
        )
        if not isinstance(val, bool):
            failures.append(
                f"states.{name}.adversarial_context must be a boolean "
                f"(true/false), got {type(val).__name__}"
            )
        if stype != "agent":
            failures.append(
                f"states.{name}.adversarial_context is only valid on "
                f"`type: agent` states; `{name}` is `type: {stype}`"
            )
    if not lines:
        lines.append("(no states declare adversarial_context)")
    return {
        "rule": 7,
        "title": "adversarial_context type and scope",
        "lines": lines,
        "result": "PASS" if not failures else "FAIL",
        "failure": None if not failures else "; ".join(failures),
    }


def _rule8_legacy_gate_keys(states):
    """Reject the pre-P4 per-gate columns. Gates now signal verdicts via the
    cadence_approve / cadence_rework labels — the two legacy keys exist only
    as an upgrade-time diagnostic."""
    lines = []
    failures = []
    for name, body in states.items():
        if not isinstance(body, dict) or body.get("type") != "gate":
            continue
        for key in LEGACY_GATE_KEYS:
            if key in body:
                line = f"states.{name}.{key} -> PRESENT (legacy)"
                lines.append(line)
                failures.append(
                    f"states.{name}.{key} is no longer supported (removed in P4). "
                    "Gates now signal verdicts via the cadence_approve / cadence_rework "
                    "labels. See CHANGELOG \"Upgrading to label-based gates\"."
                )
            else:
                lines.append(f"states.{name}.{key} -> absent")
    if not lines:
        lines.append("(no gate states defined)")
    return {
        "rule": 8,
        "title": "Legacy gate keys",
        "lines": lines,
        "result": "PASS" if not failures else "FAIL",
        "failure": None if not failures else " ".join(failures),
    }


def _build_linear_states_set(states, pickup):
    """Ordered set: pickup, then each state's linear_state. Mirrors
    tick.md step 4. Per-gate approved/rework columns are gone (P4)."""
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
    return ordered


def _build_linear_to_workflow(states, pickup):
    """Reverse lookup: Linear column name -> workflow role.

    Used by tick.md step 8 (Linear column -> matched workflow state) and
    status.md step 2 (workflow-state column rendering). Duplicates are
    first-wins; Rule 1 already fails the validation when duplicates
    exist, so the map's behaviour on duplicates only matters for
    `--evidence` callers that still proceed past a Rule 1 failure.

    Shape:
      {
        "<Linear column>": {
          "kind": "pickup" | "state" | "gate_waiting",
          "workflow_state": "<name>" | null,
          "linear_state_type": "agent" | "gate" | "terminal" | null
        }
      }
    """
    mapping = {}
    if isinstance(pickup, str) and pickup:
        mapping[pickup] = {
            "kind": "pickup",
            "workflow_state": None,
            "linear_state_type": None,
        }
    for name, body in states.items():
        if not isinstance(body, dict):
            continue
        linear_state = body.get("linear_state")
        if not isinstance(linear_state, str) or not linear_state:
            continue
        if linear_state in mapping:
            continue
        stype = body.get("type")
        kind = "gate_waiting" if stype == "gate" else "state"
        mapping[linear_state] = {
            "kind": kind,
            "workflow_state": name,
            "linear_state_type": stype if stype in ("agent", "gate", "terminal") else None,
        }
    return mapping


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
    label = wf.get("label") or {}
    limits = wf.get("limits") or {}
    entry = wf.get("entry")

    if not isinstance(states, dict):
        print("Cadence: `states:` is missing or not a mapping.", file=sys.stderr)
        sys.exit(2)

    pickup = linear.get("pickup_state") if isinstance(linear, dict) else None

    evidence = [
        _rule1_uniqueness(states, pickup),
        _rule2_entry(entry, states),
        _rule3_targets(states),
        _rule4_subagent_files(states),
        _rule5_pickup_state(linear),
        _rule6_max_in_flight(states),
        _rule7_adversarial_context(states),
        _rule8_legacy_gate_keys(states),
    ]

    failures = [ev for ev in evidence if ev["result"] == "FAIL"]
    valid = not failures

    entry_body = states.get(entry) if isinstance(entry, str) else None
    result = {
        "valid": valid,
        "entry_state_name": entry if isinstance(entry, str) else None,
        "entry_subagent": (entry_body or {}).get("subagent") if isinstance(entry_body, dict) else None,
        "workflow_linear_states": _build_linear_states_set(states, pickup),
        "linear_to_workflow": _build_linear_to_workflow(states, pickup),
        "pickup_state": pickup,
        "states": states,
        "linear": linear if isinstance(linear, dict) else {},
        "label": label if isinstance(label, dict) else {},
        "limits": limits if isinstance(limits, dict) else {},
    }

    # The dispatch prose writes its transient JSON (validator output, etc.)
    # under `.cadence/` once it has this stdout. Guarantee the scratch dir
    # and its self-ignoring `.gitignore` exist before that — on the dry-run
    # path nothing else creates it (no Linear write fires the audit hook).
    ensure_cadence_dir()

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
