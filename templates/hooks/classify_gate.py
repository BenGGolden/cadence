#!/usr/bin/env python3
"""Decide what a Cadence fire does with an issue sitting at a gate.

Caller(s):
  - templates/hooks/route_fire.py (imported; the verdict routing of the old
    tick.md step 10)

Pure function — no I/O, no MCP, no shell-out. A gate lives in exactly one
Linear column (its waiting queue); a human signals a verdict by adding the
`cadence_approve` or `cadence_rework` label. This module reads which labels
are present and returns the routing decision:

  - neither label            → **waiting**: no subagent, no comment; the
                               bootstrap just releases the lock.
  - approve only             → **approve**: target = on_approve.
  - rework only / both       → **rework**: target = on_rework. "Both present"
                               is treated as rework (the safer verdict) and
                               removes *both* labels. Escalates instead when
                               `max_rework` is defined and the prior rework
                               count has reached it.

`remove_labels` carries config label **keys** (`"cadence_approve"` /
`"cadence_rework"`), not resolved names — route_fire maps them to the
consumer's actual label strings via the validator config's `label` block.

CLI (for ad-hoc inspection / tests):
  python classify_gate.py --gate-config PATH --rework-count N \\
    [--approve] [--rework]

  --gate-config is a JSON file holding the gate's state dict
  (on_approve / on_rework / optional max_rework).

Exit code: 0 always (the decision is on stdout as JSON).
"""

import argparse
import json
import sys

APPROVE_KEY = "cadence_approve"
REWORK_KEY = "cadence_rework"


def classify_gate(approve_present, rework_present, gate_config, rework_count):
    """Return the gate routing plan.

    Shape:
      {
        "verdict": "waiting" | "approve" | "rework",
        "target_state": "<on_approve|on_rework>" | None,
        "remove_labels": ["cadence_approve" and/or "cadence_rework"],
        "escalate": bool,   # rework verdict, max_rework defined, count >= it
      }
    """
    gate_config = gate_config if isinstance(gate_config, dict) else {}

    # Rework wins when both labels are present (safer verdict).
    if rework_present:
        remove = []
        if approve_present:
            remove.append(APPROVE_KEY)
        remove.append(REWORK_KEY)
        max_rework = gate_config.get("max_rework")
        escalate = (
            isinstance(max_rework, int)
            and not isinstance(max_rework, bool)
            and rework_count >= max_rework
        )
        return {
            "verdict": "rework",
            "target_state": gate_config.get("on_rework"),
            "remove_labels": remove,
            "escalate": escalate,
        }

    if approve_present:
        return {
            "verdict": "approve",
            "target_state": gate_config.get("on_approve"),
            "remove_labels": [APPROVE_KEY],
            "escalate": False,
        }

    return {
        "verdict": "waiting",
        "target_state": None,
        "remove_labels": [],
        "escalate": False,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gate-config", required=True,
                    help="Path to a JSON file with the gate's state dict.")
    ap.add_argument("--rework-count", type=int, default=0,
                    help="Prior rework count for this gate.")
    ap.add_argument("--approve", action="store_true",
                    help="cadence_approve label is present.")
    ap.add_argument("--rework", action="store_true",
                    help="cadence_rework label is present.")
    args = ap.parse_args()

    try:
        with open(args.gate_config, "r", encoding="utf-8") as fh:
            gate_config = json.load(fh)
    except (OSError, ValueError) as e:
        print(f"Cadence: could not read --gate-config: {e}", file=sys.stderr)
        sys.exit(1)

    result = classify_gate(args.approve, args.rework, gate_config,
                           args.rework_count)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
